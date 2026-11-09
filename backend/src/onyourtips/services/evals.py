"""Evals for summaries with citations (ADR 0006, docs/evals-field-guide.md).

Four layers, cheapest first:

1. Deterministic checks (enrichment/summary_checks.py): on every summary, free.
2. Golden set + human labels: ~20 stories you judge claim by claim. The ground truth.
3. LLM-as-judge: a *different* model judges each claim against its cited passages. Cheap to run
   on everything, but only trustworthy once it agrees with your labels (Cohen's kappa).
4. Model comparison: the same golden stories summarized by several models, scored by 1-3.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.enrichment.summary_checks import SummaryChecks
from onyourtips.enrichment.summary_prompt import SUMMARY_PROMPT_VERSION
from onyourtips.enrichment.summary_schema import Summary
from onyourtips.llm import LLMError, LLMGateway, LLMRequest
from onyourtips.llm.models import Message
from onyourtips.services.summaries import plan_summary, summarize_article
from onyourtips.storage import Database

VERDICTS = ("supported", "partial", "unsupported")
VERDICT_SCORE = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
HUMAN = "human"

JUDGE_PROMPT_VERSION = "judge-v1"
JUDGE_SYSTEM = """You check whether a claim is supported by source passages.
Verdicts:
- supported: everything the claim says is stated in the passages.
- partial: some of it is stated, but part is missing, overstated or changed.
- unsupported: the passages don't say it, or say something different.
Judge only against the passages, never your own knowledge. Ignore style.
Return JSON only: {"reason": "one sentence, pointing at the passage", "verdict": "supported"}"""


class Verdict(BaseModel):
    reason: str  # asked for *before* the verdict: the model explains first, then decides
    verdict: Literal["supported", "partial", "unsupported"]


# ----------------------------------------------------------------------------- golden set

BUCKET_SHARE = {"short": 0.4, "medium": 0.4, "long": 0.2}


def _bucket(plan) -> str | None:
    if not plan.fits:
        return None
    if plan.mode == "map_reduce":
        return "long"
    return "short" if plan.estimated_input_tokens < 900 else "medium"


def pick_golden(settings: Settings, db: Database, n: int = 20) -> list[dict]:
    """Stratified pick: 40% short, 40% medium, 20% long (map-reduce) stories, mixing content
    types, highest signal first. Deterministic, so the set is reproducible."""
    have = {g["article_id"] for g in db.golden()}
    pools: dict[str, list] = {"short": [], "medium": [], "long": []}
    for article_id, _ in db.usable_contents():
        if article_id in have:
            continue
        b = _bucket(plan_summary(settings, db, article_id))
        if b:
            art = db.get_article(article_id)
            pools[b].append((art.content_type.value, -art.signal, article_id))
    added = []
    for bucket, share in BUCKET_SHARE.items():
        want = max(1, round(n * share))
        by_type: dict[str, list] = {}
        for t, _neg_signal, aid in sorted(pools[bucket]):
            by_type.setdefault(t, []).append(aid)
        # round-robin across content types (news, project, paper ...) for variety
        picked: list[str] = []
        while len(picked) < want and any(by_type.values()):
            for t in sorted(by_type):
                if by_type[t] and len(picked) < want:
                    picked.append(by_type[t].pop(0))
        for aid in picked:
            db.add_golden(aid, bucket)
            added.append({"article_id": aid, "bucket": bucket})
    return added


def golden_summaries(
    settings: Settings,
    db: Database,
    model: str | None = None,
    gateway: LLMGateway | None = None,
    *,
    create: bool = True,
) -> list[dict]:
    """The current successful summary of each golden story for this model (summarized now if
    missing and `create`)."""
    model = model or settings.llm_model
    rows = []
    for g in db.golden():
        row = db.latest_summary(g["article_id"], model, SUMMARY_PROMPT_VERSION)
        if row is None and create:
            out = summarize_article(
                settings, db, g["article_id"], model=model, gateway=gateway, trigger="eval"
            )
            row = db.get_summary(out.summary_id) if out.done else None
        if row:
            rows.append({**row, "bucket": g["bucket"]})
    return rows


class ClaimItem(BaseModel):
    summary_id: int
    point_idx: int
    title: str
    claim: str
    quote: str
    citations: list[int]
    passages: dict[int, str]


def claims_of(db: Database, summary_row: dict) -> list[ClaimItem]:
    s = Summary.model_validate_json(summary_row["summary_json"])
    by_idx = {p.idx: p.text for p in db.get_passages(summary_row["content_hash"])}
    title = db.get_article(summary_row["article_id"]).title
    return [
        ClaimItem(
            summary_id=summary_row["id"],
            point_idx=i,
            title=title,
            claim=kp.text,
            quote=kp.quote,
            citations=kp.citations,
            passages={c: by_idx[c] for c in kp.citations if c in by_idx},
        )
        for i, kp in enumerate(s.key_points, 1)
    ]


def label_queue(settings: Settings, db: Database, model: str | None = None) -> list[ClaimItem]:
    """Golden claims you haven't labelled yet."""
    rows = golden_summaries(settings, db, model, create=False)
    done = {(j["summary_id"], j["point_idx"]) for j in db.judgments(HUMAN, [r["id"] for r in rows])}
    return [c for r in rows for c in claims_of(db, r) if (c.summary_id, c.point_idx) not in done]


def save_label(
    db: Database, item: ClaimItem, verdict: str, note: str | None = None, judge: str = HUMAN
) -> None:
    db.save_judgment(
        {
            "summary_id": item.summary_id,
            "point_idx": item.point_idx,
            "judge": judge,
            "verdict": verdict,
            "reason": note,
            "created_at": utcnow().isoformat(timespec="seconds"),
        }
    )


# ----------------------------------------------------------------------------- LLM judge


def judge_claim(gw: LLMGateway, item: ClaimItem, judge_model: str, article_id: str) -> Verdict:
    passages = "\n\n".join(f"[P{i}] {t}" for i, t in sorted(item.passages.items()))
    user = f"Passages:\n{passages}\n\nClaim: {item.claim}"
    req = LLMRequest(
        purpose="judge",
        model=judge_model,
        prompt_version=JUDGE_PROMPT_VERSION,
        article_id=article_id,
        temperature=0.0,
        max_tokens=200,
        messages=[Message(role="system", content=JUDGE_SYSTEM), Message(role="user", content=user)],
    )
    return gw.complete(req, schema=Verdict).data


class JudgeRun(BaseModel):
    judge_model: str
    judged: int = 0
    skipped_existing: int = 0
    errors: int = 0
    self_judging: bool = False  # judge == summarizer: biased toward its own output


def judge_summaries(
    settings: Settings,
    db: Database,
    judge_model: str,
    summary_rows: list[dict],
    gateway: LLMGateway | None = None,
) -> JudgeRun:
    gw = gateway or LLMGateway(settings.model_copy(update={"llm_retries": 1}), db)
    run = JudgeRun(
        judge_model=judge_model, self_judging=any(r["model"] == judge_model for r in summary_rows)
    )
    have = {
        (j["summary_id"], j["point_idx"])
        for j in db.judgments(judge_model, [r["id"] for r in summary_rows])
    }
    for row in summary_rows:
        for item in claims_of(db, row):
            if (item.summary_id, item.point_idx) in have:
                run.skipped_existing += 1
                continue
            before = db.max_llm_call_id()
            try:
                v = judge_claim(gw, item, judge_model, row["article_id"])
            except LLMError:
                run.errors += 1
                continue
            db.save_judgment(
                {
                    "summary_id": item.summary_id,
                    "point_idx": item.point_idx,
                    "judge": judge_model,
                    "verdict": v.verdict,
                    "reason": v.reason,
                    "prompt_version": JUDGE_PROMPT_VERSION,
                    "llm_call_id": db.max_llm_call_id() if db.max_llm_call_id() > before else None,
                    "created_at": utcnow().isoformat(timespec="seconds"),
                }
            )
            run.judged += 1
    return run


# ----------------------------------------------------------------------------- agreement


class Agreement(BaseModel):
    judge: str
    reference: str
    n: int  # claims judged by both
    accuracy: float | None = None  # share of identical verdicts
    kappa: float | None = None  # agreement beyond chance: 1 perfect, 0 chance, <0 worse
    confusion: dict[str, dict[str, int]] = {}  # reference verdict -> judge verdict -> count
    unsupported_recall: float | None = None  # of claims you marked unsupported, share it caught
    unsupported_precision: float | None = None  # of its 'unsupported', share you agree with


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two raters over the same items."""
    n = len(pairs)
    if not n:
        return None
    p_o = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    p_e = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if p_e == 1 else round((p_o - p_e) / (1 - p_e), 3)


def agreement(db: Database, judge: str, reference: str = HUMAN) -> Agreement:
    ref = {(j["summary_id"], j["point_idx"]): j["verdict"] for j in db.judgments(reference)}
    pairs = [
        (ref[k], j["verdict"])
        for j in db.judgments(judge)
        if (k := (j["summary_id"], j["point_idx"])) in ref
    ]
    a = Agreement(judge=judge, reference=reference, n=len(pairs))
    if not pairs:
        return a
    a.accuracy = round(sum(x == y for x, y in pairs) / len(pairs), 3)
    a.kappa = cohen_kappa(pairs)
    a.confusion = {
        r: {j: sum(1 for x, y in pairs if x == r and y == j) for j in VERDICTS} for r in VERDICTS
    }
    ref_uns = [p for p in pairs if p[0] == "unsupported"]
    judge_uns = [p for p in pairs if p[1] == "unsupported"]
    if ref_uns:
        a.unsupported_recall = round(sum(y == "unsupported" for _, y in ref_uns) / len(ref_uns), 3)
    if judge_uns:
        a.unsupported_precision = round(
            sum(x == "unsupported" for x, _ in judge_uns) / len(judge_uns), 3
        )
    return a


def faithfulness(db: Database, judge: str, summary_ids: list[int]) -> tuple[float | None, int]:
    """Average claim score (supported 1, partial 0.5, unsupported 0) and claims judged."""
    js = db.judgments(judge, summary_ids)
    if not js:
        return None, 0
    return round(sum(VERDICT_SCORE[j["verdict"]] for j in js) / len(js), 3), len(js)


# ----------------------------------------------------------------------------- comparison


class ModelScore(BaseModel):
    model: str
    stories: int
    summarized: int
    success_rate: float
    avg_seconds: float | None
    avg_input_tokens: int | None
    avg_output_tokens: int | None
    quote_match: float | None
    coverage: float | None
    repaired: int
    faithfulness_judge: float | None
    claims_judged: int
    faithfulness_human: float | None
    usefulness_human: float | None


def score_model(
    settings: Settings, db: Database, model: str, judge_model: str | None, gateway_for=None
) -> ModelScore:
    gw = gateway_for(model) if gateway_for else None
    golden = db.golden()
    rows = golden_summaries(settings, db, model, gw)
    if judge_model:
        judge_summaries(
            settings, db, judge_model, rows, gateway_for(judge_model) if gateway_for else None
        )
    ids = [r["id"] for r in rows]
    checks = [SummaryChecks.model_validate_json(r["checks_json"]) for r in rows if r["checks_json"]]
    quoted = [c for c in checks if c.quotes]
    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    fj, nj = faithfulness(db, judge_model, ids) if judge_model else (None, 0)
    fh, _ = faithfulness(db, HUMAN, ids)
    ratings = db.ratings(HUMAN)
    use = [ratings[i] for i in ids if i in ratings]
    return ModelScore(
        model=model,
        stories=len(golden),
        summarized=len(rows),
        success_rate=round(len(rows) / len(golden), 3) if golden else 0.0,
        avg_seconds=round(sum(lat) / len(lat) / 1000, 1) if lat else None,
        avg_input_tokens=round(sum(r["real_input_tokens"] or 0 for r in rows) / len(rows))
        if rows
        else None,
        avg_output_tokens=round(sum(r["real_output_tokens"] or 0 for r in rows) / len(rows))
        if rows
        else None,
        quote_match=round(sum(c.quote_match_rate for c in quoted) / len(quoted), 3)
        if quoted
        else None,
        coverage=round(sum(c.coverage for c in checks) / len(checks), 3) if checks else None,
        repaired=sum(1 for c in checks if c.invalid_citations_removed),
        faithfulness_judge=fj,
        claims_judged=nj,
        faithfulness_human=fh,
        usefulness_human=round(sum(use) / len(use), 2) if use else None,
    )


def _fmt(v, pct=False):
    if v is None:
        return "–"
    return f"{v:.0%}" if pct else (f"{v:g}" if isinstance(v, float) else str(v))


def write_compare_report(
    scores: list[ModelScore],
    judge_model: str | None,
    agreement_: Agreement | None,
    out_dir: Path,
    now: datetime | None = None,
) -> Path:
    now = now or utcnow()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"summary-compare-{now:%Y%m%d-%H%M}.md"
    lines = [
        f"# Summary model comparison — {now:%Y-%m-%d %H:%M} UTC",
        "",
        f"Golden set: {scores[0].stories if scores else 0} stories"
        f" · prompt `{SUMMARY_PROMPT_VERSION}` · judge: `{judge_model or 'none'}`",
        "",
        "| Model | Summarized | Faithful (judge) | Faithful (you) | Useful (you, 1–5) "
        "| Quotes found | Coverage | Repaired | Avg time | Avg tokens in / out |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        lines.append(
            f"| `{s.model}` | {s.summarized}/{s.stories} | {_fmt(s.faithfulness_judge, True)} "
            f"| {_fmt(s.faithfulness_human, True)} | {_fmt(s.usefulness_human)} "
            f"| {_fmt(s.quote_match, True)} | {_fmt(s.coverage, True)} | {s.repaired} "
            f"| {_fmt(s.avg_seconds)} s "
            f"| {_fmt(s.avg_input_tokens)} / {_fmt(s.avg_output_tokens)} |"
        )
    if agreement_ and agreement_.n:
        a = agreement_
        lines += [
            "",
            f"## Can the judge be trusted? (`{a.judge}` vs your labels, {a.n} claims)",
            "",
            f"- Accuracy {_fmt(a.accuracy, True)}, Cohen's kappa **{_fmt(a.kappa)}** "
            "(≥ 0.6 substantial, 0.4–0.6 moderate, < 0.4 don't rely on it)",
            f"- Catches {_fmt(a.unsupported_recall, True)} of the claims you marked unsupported; "
            f"{_fmt(a.unsupported_precision, True)} of its 'unsupported' calls are right",
            "",
            "| You \\ judge | " + " | ".join(VERDICTS) + " |",
            "|---|---|---|---|",
        ]
        for r in VERDICTS:
            lines.append(f"| {r} | " + " | ".join(str(a.confusion[r][j]) for j in VERDICTS) + " |")
    ranked = sorted(
        (s for s in scores if s.summarized),
        key=lambda s: (-(s.faithfulness_human or s.faithfulness_judge or 0), s.avg_seconds or 1e9),
    )
    if ranked:
        best = ranked[0]
        lines += [
            "",
            "## Judgement",
            "",
            f"Most faithful: `{best.model}`. Rank by faithfulness first (a wrong summary is "
            "worse than a slow one), then time per story: at ~500 stories a night, "
            f"{_fmt(best.avg_seconds)} s each is about "
            f"{(best.avg_seconds or 0) * 500 / 3600:.1f} hours of laptop time.",
            "Numbers from fewer than ~20 stories are noisy: treat gaps under ~10 points as ties.",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def judge_rows_for_window(db: Database, model: str, limit: int) -> list[dict]:
    """Recent successful summaries (any story), for judging at scale once the judge is trusted."""
    rows = db.conn.execute(
        "SELECT * FROM summaries WHERE model=? AND summarization_done='yes' AND prompt_version=? "
        "ORDER BY id DESC LIMIT ?",
        (model, SUMMARY_PROMPT_VERSION, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def to_json(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
