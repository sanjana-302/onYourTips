"""Token counts for summaries: free estimates, calibration with a (fake) model, the report."""

import pytest

from onyourtips.core.models import ArticleContent, ContentStatus
from onyourtips.enrichment.passages import content_hash, normalize_text, split_passages
from onyourtips.enrichment.summary_prompt import SUMMARY_PROMPT_VERSION, SUMMARY_SYSTEM
from onyourtips.enrichment.tokens import estimate_tokens, percentile
from onyourtips.llm import LLMGateway
from onyourtips.llm.models import ProviderResponse, Usage
from onyourtips.llm.pricing import Price, Pricing
from onyourtips.services import tokens as svc
from tests.helpers import HN, NOW, W
from tests.helpers import normalize_raw as _norm

SENTENCE = "Engineers released a faster open model for coding agents and published the weights. "


def add_article(db, tagger, slug, paragraphs, status=ContentStatus.OK):
    a, sighting = _norm(tagger, HN, url=f"https://news.test/{slug}", title=f"Story {slug} title")
    db.upsert(a, sighting, W)
    text = normalize_text("\n\n".join(paragraphs))
    passages = split_passages(text)
    db.save_content(
        ArticleContent(
            article_id=a.id,
            content_hash=content_hash(text),
            extractor="trafilatura",
            extractor_version="test",
            source_url=a.url,
            text=text,
            word_count=len(text.split()),
            status=status,
            fetched_at=NOW,
        ),
        passages,
    )
    return a.id


class CountingProvider:
    """Pretends to be a model whose tokenizer averages 3.5 characters per token."""

    name = "fake"

    def __init__(self, cache_hit_for=()):
        self.calls = []
        self.cache_hit_for = cache_hit_for

    def chat(self, request, model):
        text = request.messages[-1].content
        self.calls.append(request)
        tokens = 2 if any(k in text for k in self.cache_hit_for) else round(len(text) / 3.5)
        return ProviderResponse(
            text=".", usage=Usage(input_tokens=tokens, output_tokens=1), model=model
        )

    def list_models(self):
        return []

    def version(self):
        return "test"


@pytest.fixture
def corpus(settings, db, tagger):
    settings.llm_model = "m1"
    ids = {
        "short": add_article(db, tagger, "short", [SENTENCE * 3], ContentStatus.EXCERPT),
        "mid": add_article(db, tagger, "mid", [SENTENCE * 12] * 4),
        "long": add_article(db, tagger, "long", [SENTENCE * 13] * 8),
        "huge": add_article(db, tagger, "huge", [SENTENCE * 13] * 60),
    }
    return settings, db, ids


def test_helpers():
    assert estimate_tokens(0) == 0 and estimate_tokens(9, 4.0) == 3
    assert percentile([], 50) == 0
    assert percentile([5, 1, 3, 2, 4], 50) == 3 and percentile([1, 2, 3, 4], 90) == 4


def test_estimates_and_report(corpus):
    settings, db, ids = corpus
    k = svc.estimate_articles(settings, db)
    assert k == {"estimated": 4, "tokenizer": "ratio:4.00", "calibration_samples": 0}
    assert svc.estimate_articles(settings, db)["estimated"] == 4  # re-running replaces rows

    rows = {r["article_id"]: r for r in db.token_counts("m1", SUMMARY_PROMPT_VERSION, "estimate")}
    assert len(rows) == 4
    mid = rows[ids["mid"]]
    assert mid["system_chars"] == len(SUMMARY_SYSTEM)
    assert mid["article_chars"] > len(SENTENCE) * 48  # numbered passages + title included
    assert mid["input_tokens"] == mid["system_tokens"] + mid["article_tokens"] + 30

    r = svc.token_report(settings, db, days=3650)
    assert r.articles == 4 and r.excerpt_articles == 1 and not r.calibrated
    assert r.max_input == rows[ids["huge"]]["input_tokens"] and r.max_title == "Story huge title"
    assert r.over_ctx == 1  # the huge one doesn't fit 8192 tokens
    assert r.projections == []  # nothing priced


def test_calibration_sets_the_ratio(corpus):
    settings, db, ids = corpus
    svc.estimate_articles(settings, db)
    provider = CountingProvider()
    gw = LLMGateway(
        settings,
        db,
        provider=provider,
        pricing=Pricing("t", {"fake": {"*": Price()}}),
        sleep=lambda s: None,
    )

    res = svc.calibrate(settings, db, sample=10, gateway=gw)
    # short is too short to measure reliably, huge too long; mid + long are measured
    assert res.measured == 2 and res.skipped == 0
    assert res.system_tokens == round(len(SUMMARY_SYSTEM) / 3.5)
    assert res.chars_per_token == pytest.approx(3.5, abs=0.01) and res.re_estimated == 4
    # one tiny output token per call, context sized to the article
    assert all(c.max_tokens == 1 for c in provider.calls)
    assert all(len(c.messages) == 1 for c in provider.calls)  # measured one message at a time
    assert {c.purpose for c in provider.calls} == {"token_count"}

    r = svc.token_report(settings, db, days=3650)
    assert r.calibrated and r.chars_per_token == 3.5 and r.calibration_samples == 2
    assert r.system_tokens == res.system_tokens  # measured, not estimated
    assert r.estimate_error_pct is not None and r.estimate_error_pct < 1

    # running again measures nothing new: those articles are already counted
    again = svc.calibrate(settings, db, sample=10, gateway=gw)
    assert again.measured == 0 and again.previous_chars_per_token == pytest.approx(3.5, abs=0.01)


def test_cache_hit_counts_are_discarded(corpus):
    settings, db, ids = corpus
    gw = LLMGateway(
        settings,
        db,
        provider=CountingProvider(cache_hit_for=("Story long",)),
        pricing=Pricing("t", {"fake": {"*": Price()}}),
        sleep=lambda s: None,
    )
    res = svc.calibrate(settings, db, sample=10, gateway=gw)
    assert res.measured == 1 and res.skipped == 1


def test_projection_uses_priced_models(corpus, tmp_path):
    settings, db, ids = corpus
    prices = tmp_path / "pricing.yaml"
    prices.write_text(
        'version: "t"\nproviders:\n  ollama:\n    "*": { input: 0, output: 0 }\n'
        "  paid:\n    big-model: { input: 2.0, output: 10.0 }\n"
    )
    settings.pricing_file = prices
    svc.estimate_articles(settings, db)
    r = svc.token_report(settings, db, days=3650)
    [p] = r.projections
    assert (p.provider, p.model) == ("paid", "big-model")
    expected = (r.p50_input * 2.0 + r.output_budget * 10.0) / 1_000_000
    assert p.per_article_usd == pytest.approx(expected, rel=1e-3)
    assert p.per_week_digest_usd == pytest.approx(30 * expected, rel=1e-2)


# ------------------------------------------------------------------------- exact tokenizers


class FakeEncoding:
    def __init__(self, ids):
        self.ids = ids


class WordTokenizer:
    """Stands in for a Hugging Face tokenizer: one token per word."""

    def encode(self, text, add_special_tokens=True):
        assert add_special_tokens is False  # template tokens are added separately
        return FakeEncoding(text.split())


def test_tokenizer_lookup(tmp_path):
    from onyourtips.enrichment.tokens import tokenizer_repo

    cfg = tmp_path / "tokenizers.yaml"
    cfg.write_text("models:\n  llama3.2: org/llama\n  qwen3.5:4b: org/qwen-4b\n")
    assert tokenizer_repo(cfg, "llama3.2:3b") == "org/llama"  # family match
    assert tokenizer_repo(cfg, "qwen3.5:4b") == "org/qwen-4b"  # exact match
    assert tokenizer_repo(cfg, "granite4.2:3b") is None
    assert tokenizer_repo(tmp_path / "missing.yaml", "llama3.2:3b") is None


def test_hf_counter_is_used_when_available(corpus, monkeypatch):
    from onyourtips.enrichment import tokens as tk

    settings, db, ids = corpus
    settings.llm_model = "llama3.2:3b"  # mapped in config/tokenizers.yaml
    monkeypatch.setattr(svc, "load_hf_tokenizer", lambda repo: WordTokenizer())
    k = svc.estimate_articles(settings, db)
    assert k["tokenizer"] == "hf:unsloth/Llama-3.2-3B-Instruct"
    row = next(
        r
        for r in db.token_counts("llama3.2:3b", SUMMARY_PROMPT_VERSION, "estimate")
        if r["article_id"] == ids["mid"]
    )
    assert row["system_tokens"] == len(SUMMARY_SYSTEM.split())
    assert row["input_tokens"] == row["system_tokens"] + row["article_tokens"] + 30
    assert row["tokenizer"] == k["tokenizer"]

    # tokenizer can't load (offline, not installed) -> falls back to the ratio, still works
    monkeypatch.setattr(svc, "load_hf_tokenizer", lambda repo: None)
    assert svc.estimate_articles(settings, db)["tokenizer"] == "ratio:4.00"
    assert tk.HFCounter(WordTokenizer(), "x").count("") == 0


# ------------------------------------------------------------------------- summaries table


def test_summary_plan_and_records(corpus, tmp_path):
    from onyourtips.services.summaries import list_summaries, plan_summary, record_summary

    settings, db, ids = corpus
    prices = tmp_path / "pricing.yaml"
    prices.write_text('version: "t"\nproviders:\n  ollama:\n    m1: { input: 1.0, output: 5.0 }\n')
    settings.pricing_file = prices

    plan = plan_summary(settings, db, ids["mid"][:8])  # id prefix works
    assert plan.fits and plan.reason is None and plan.tokenizer == "ratio:4.00"
    assert plan.estimated_output_tokens == 400
    expected = (plan.estimated_input_tokens * 1.0 + 400 * 5.0) / 1_000_000
    assert plan.estimated_cost_usd == pytest.approx(expected)
    req = plan.request(temperature=0.0)
    assert req.purpose == "summarize" and req.max_tokens == 400 and len(req.messages) == 2
    assert "[P1]" in req.messages[1].content

    # success: real numbers from the response (a real call through the gateway + ledger)
    gw = LLMGateway(
        settings,
        db,
        provider=CountingProvider(),
        pricing=Pricing("t", {"fake": {"*": Price()}}),
        sleep=lambda s: None,
    )
    result = gw.complete(req)
    record_summary(db, plan, result=result, data={"tldr": "x"})
    # failure after the call reached the model (e.g. invalid JSON): done = no, reason kept
    record_summary(db, plan, error="invalid: key_points missing citations")

    # too long for one pass: planned as map-reduce, every chunk inside the context window
    huge = plan_summary(settings, db, ids["huge"])
    assert huge.fits and huge.mode == "map_reduce" and len(huge.chunks) >= 2
    assert huge.estimated_output_tokens == len(huge.chunks) * 300 + 400
    assert sorted(i for c in huge.chunks for i in c) == list(
        range(1, len([i for c in huge.chunks for i in c]) + 1)
    )  # every passage once, in order

    rows = list_summaries(db)
    by_desc = {r["description"]: r for r in rows}
    ok = by_desc[None]
    assert ok["summarization_done"] == "yes"
    assert ok["real_input_tokens"] == result.usage.input_tokens and ok["real_output_tokens"] == 1
    assert ok["llm_call_id"] == result.call_id and ok["latency_ms"] is not None
    assert ok["real_cost_usd"] == 0.0 and ok["estimated_cost_usd"] == pytest.approx(expected)
    assert ok["summary_json"] == '{"tldr": "x"}'
    bad = by_desc["invalid: key_points missing citations"]
    assert bad["summarization_done"] == "no" and bad["summary_json"] is None
    assert len(list_summaries(db, ids["mid"][:8])) == 2


def test_summary_plan_without_text(settings, db, tagger):
    from onyourtips.services.summaries import plan_summary

    a, sighting = _norm(tagger, HN, url="https://news.test/none", title="No text for this one")
    db.upsert(a, sighting, W)
    plan = plan_summary(settings, db, a.id)
    assert not plan.fits and plan.reason == "no usable text (not extracted)"
