"""Summaries end to end with a scripted fake model: single pass, map-reduce, failures,
the nightly step with its deadline, the cost report, and the eval loop."""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from onyourtips.core.models import ContentStatus
from onyourtips.enrichment.summary_prompt import SUMMARY_PROMPT_VERSION
from onyourtips.llm import LLMGateway
from onyourtips.llm.models import ModelInfo, ProviderResponse, Usage
from onyourtips.llm.pricing import Price, Pricing
from onyourtips.services import evals
from onyourtips.services import summaries as svc
from tests.integration.test_tokens import SENTENCE, add_article

PASSAGE = re.compile(r"\[P(\d+)\] (.+)")
NOTE = re.compile(r'\[N\d+\] (.+?) \(from ([P0-9, ]+); quote: "(.*)"\)')


class ScriptedModel:
    """Answers like a well-behaved model, unless told to misbehave."""

    name = "fake"

    def __init__(self, mode="good", models=("m1",)):
        self.mode = mode
        self.models = models
        self.calls = []

    def _points(self, text, n):
        found = PASSAGE.findall(text)[:n]
        return [
            {
                "text": f"Point about {t[:40]}",
                "citations": [int(i)],
                "quote": " ".join(t.split()[:6]),
            }
            for i, t in found
        ]

    def chat(self, request, model):
        self.calls.append(request)
        user = request.messages[-1].content
        if request.purpose == "summarize_map":
            out = {"points": self._points(user, 2)}
        elif request.purpose == "summarize_reduce":
            notes = NOTE.findall(user)
            out = {
                "tldr": "Merged takeaway.",
                "why_it_matters": "Because.",
                "key_points": [
                    {"text": t, "citations": [int(c) for c in re.findall(r"\d+", cs)], "quote": q}
                    for t, cs, q in notes[:5]
                ],
            }
        elif request.purpose == "judge":
            claim = user.split("Claim: ", 1)[1]
            verdict = "unsupported" if "INVENTED" in claim else "supported"
            out = {"reason": "checked the passage", "verdict": verdict}
        else:
            pts = self._points(user, 3)
            if self.mode == "bad_citations":
                pts = [{**p, "citations": [999]} for p in pts]
            if self.mode == "invent":
                pts[0]["text"] = "INVENTED " + pts[0]["text"]
            out = {"tldr": "Takeaway.", "key_points": pts, "why_it_matters": "Because."}
        text = "not json" if self.mode == "garbage" else json.dumps(out)
        return ProviderResponse(
            text=text, model=model, usage=Usage(input_tokens=len(user) // 4, output_tokens=120)
        )

    def list_models(self):
        return [ModelInfo(name=m) for m in self.models]

    def version(self):
        return "test"


def gateway(settings, db, model=None):
    s = settings.model_copy(update={"llm_retries": 0})
    return LLMGateway(
        s,
        db,
        provider=model or ScriptedModel(),
        pricing=Pricing("t", {"fake": {"*": Price()}}),
        sleep=lambda s: None,
    )


@pytest.fixture
def corpus(settings, db, tagger):
    settings.llm_model = "m1"
    ids = {
        "a": add_article(db, tagger, "a", [SENTENCE * 3, SENTENCE * 2, SENTENCE]),
        "b": add_article(db, tagger, "b", [SENTENCE * 4] * 3, ContentStatus.EXCERPT),
        "huge": add_article(db, tagger, "huge", [SENTENCE * 13] * 60),
    }
    return settings, db, ids


def test_single_pass_summary_is_checked_recorded_and_cached(corpus):
    settings, db, ids = corpus
    gw = gateway(settings, db)
    out = svc.summarize_article(settings, db, ids["a"], gateway=gw)
    assert out.done and out.mode == "single" and not out.cached
    assert out.checks.quote_match_rate == 1.0 and out.checks.invalid_citations_removed == 0

    row = db.get_summary(out.summary_id)
    assert row["summarization_done"] == "yes" and row["calls"] == 1
    assert row["real_input_tokens"] > 0 and row["estimated_input_tokens"] > 0
    assert row["trigger"] == "on_demand" and json.loads(row["checks_json"])["passed"]

    again = svc.summarize_article(settings, db, ids["a"], gateway=gw)
    assert again.cached and again.summary_id == out.summary_id
    assert len(gw.provider.calls) == 1  # no second model call

    view = svc.get_summary_view(settings, db, ids["a"][:8])
    assert view.status == "ready" and len(view.key_points) == 3
    assert {p.idx for p in view.passages} == {c for k in view.key_points for c in k.citations}


def test_map_reduce_for_long_story(corpus):
    settings, db, ids = corpus
    gw = gateway(settings, db)
    out = svc.summarize_article(settings, db, ids["huge"], gateway=gw)
    assert out.done and out.mode == "map_reduce"
    maps = [c for c in gw.provider.calls if c.purpose == "summarize_map"]
    reduces = [c for c in gw.provider.calls if c.purpose == "summarize_reduce"]
    assert len(maps) >= 2 and len(reduces) == 1
    assert all(c.num_ctx == settings.llm_num_ctx for c in maps)
    row = db.get_summary(out.summary_id)
    assert row["calls"] == len(maps) + 1 and row["chunks"] == len(maps)
    # citations in the final summary point at real passages of the long story
    assert out.checks.passed and out.checks.cited_passages >= 2


def test_map_reduce_cap_marks_partial(corpus):
    settings, db, ids = corpus
    settings.summary_max_chunks = 1
    plan = svc.plan_summary(settings, db, ids["huge"])
    assert plan.partial and len(plan.chunks) == 1 and plan.total_chunks >= 2
    out = svc.summarize_article(settings, db, ids["huge"], gateway=gateway(settings, db))
    assert out.done and db.get_summary(out.summary_id)["partial"] == 1


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("garbage", "didn't match Summary"),
        ("bad_citations", "no key point cites a real passage"),
    ],
)
def test_failures_are_recorded_with_a_reason(corpus, mode, reason):
    settings, db, ids = corpus
    out = svc.summarize_article(
        settings, db, ids["a"], gateway=gateway(settings, db, ScriptedModel(mode))
    )
    assert not out.done and reason in out.description
    row = db.get_summary(out.summary_id)
    assert row["summarization_done"] == "no" and reason in row["description"]
    assert row["calls"] == 1 and row["summary_json"] is None
    assert svc.get_summary_view(settings, db, ids["a"]).status == "failed"


def test_nightly_step_until_deadline_and_report(corpus, monkeypatch):
    from onyourtips.services import nightly

    settings, db, ids = corpus
    settings.summary_deadline = "08:30"
    t = [datetime(2026, 11, 9, 1, 0).astimezone()]  # 1 AM on this machine's clock

    def clock():
        t[0] += timedelta(hours=2)  # each story "takes" 2 hours: only 2 fit before 08:30
        return t[0]

    report = nightly.NightlyReport(
        run_id="r1", trigger="scheduled", started_at=datetime.now(timezone.utc)
    )
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    problems = nightly._summarize(settings, db, report, since, gateway(settings, db), clock)
    m = report.summaries
    assert m["candidates"] == 3 and m["done"] == 2 and m["not_reached"] == 1 and problems
    assert [a.kind for a in report.new_alerts] == ["summaries_unfinished"]

    # next night: only the remaining story is a candidate
    t[0] = datetime(2026, 11, 10, 1, 0).astimezone()
    report2 = nightly.NightlyReport(
        run_id="r2", trigger="scheduled", started_at=datetime.now(timezone.utc)
    )
    nightly._summarize(settings, db, report2, since, gateway(settings, db), clock)
    assert report2.summaries["candidates"] == 1 and report2.summaries["done"] == 1

    r = svc.summary_report(settings, db, days=1)
    assert r.attempts == 3 and r.done == 3 and r.map_reduce >= 0
    assert r.input_tokens > 0 and r.real_cost_usd == 0.0
    assert r.estimate_error_pct is not None


def test_nightly_skips_with_one_alert_when_model_missing(corpus):
    from onyourtips.services import nightly

    settings, db, ids = corpus
    report = nightly.NightlyReport(
        run_id="r1", trigger="scheduled", started_at=datetime.now(timezone.utc)
    )
    gw = gateway(settings, db, ScriptedModel(models=("other:1b",)))
    assert nightly._summarize(
        settings, db, report, datetime(2000, 1, 1, tzinfo=timezone.utc), gw, None
    )
    assert "isn't downloaded" in report.summaries["skipped"]
    assert [a.kind for a in report.new_alerts] == ["llm_unavailable"]
    assert db.list_summaries() == []


def test_eval_loop_golden_labels_judge_agreement_compare(corpus, tmp_path):
    settings, db, ids = corpus
    added = evals.pick_golden(settings, db, n=3)
    buckets = {a["bucket"] for a in added}
    assert "long" in buckets and len(db.golden()) == len(added)

    # summarize the golden set with a model that invents one claim per summary
    gw = gateway(settings, db, ScriptedModel("invent"))
    rows = evals.golden_summaries(settings, db, "m1", gw)
    assert len(rows) == len(added)

    # you label: the INVENTED claims are unsupported, everything else supported
    queue = evals.label_queue(settings, db)
    for item in queue:
        evals.save_label(db, item, "unsupported" if "INVENTED" in item.claim else "supported")
    assert evals.label_queue(settings, db) == []

    # the judge (a different model) catches the invented claims
    run = evals.judge_summaries(settings, db, "judge-model", rows, gateway(settings, db))
    assert run.judged == len(queue) and not run.self_judging
    a = evals.agreement(db, "judge-model")
    assert a.n == len(queue) and a.accuracy == 1.0 and a.kappa == 1.0
    assert a.unsupported_recall == 1.0

    f, n = evals.faithfulness(db, evals.HUMAN, [r["id"] for r in rows])
    assert 0 < f < 1 and n == len(queue)

    score = evals.score_model(
        settings, db, "m1", "judge-model", gateway_for=lambda m: gateway(settings, db)
    )
    assert score.summarized == len(added) and score.faithfulness_judge == pytest.approx(f)
    path = evals.write_compare_report([score], "judge-model", a, tmp_path)
    text = path.read_text()
    assert "Cohen's kappa" in text and "## Judgement" in text and "`m1`" in text


def test_summary_prompt_version_is_part_of_the_cache_key(corpus):
    settings, db, ids = corpus
    out = svc.summarize_article(settings, db, ids["a"], gateway=gateway(settings, db))
    assert db.latest_summary(ids["a"], "m1", SUMMARY_PROMPT_VERSION)["id"] == out.summary_id
    assert db.latest_summary(ids["a"], "m1", "summary-v0") is None
    assert db.latest_summary(ids["a"], "other-model") is None
