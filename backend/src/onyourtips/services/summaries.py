"""Summaries with citations (ADR 0002, ADR 0006).

    plan_summary       build the prompt, count tokens, estimate cost, choose single pass or
                       map-reduce. No model call.
    summarize_article  run the plan through the LLM gateway, repair + check the output,
                       record one `summaries` row (done yes/no, estimated vs real cost)
    summary_report     what summarizing cost: calls, tokens, laptop time, estimate accuracy,
                       projected cost on paid models

Map-reduce for long articles: passages are grouped into chunks that fit the context window;
MAP turns each chunk into a few cited notes, REDUCE merges the notes into the final summary.
Passage numbers are global, so the final citations still point at real passages. Very long
articles are capped at `summary_max_chunks` chunks and marked partial.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from onyourtips.core.models import Passage
from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.enrichment.summary_checks import SummaryChecks, check_summary
from onyourtips.enrichment.summary_prompt import (
    MAP_SYSTEM,
    REDUCE_SYSTEM,
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SYSTEM,
    map_user_message,
    reduce_user_message,
    summary_user_message,
)
from onyourtips.enrichment.summary_schema import KeyPoint, MapNotes, Summary
from onyourtips.enrichment.tokens import TEMPLATE_OVERHEAD_TOKENS, TokenCounter
from onyourtips.llm import LLMError, LLMGateway, LLMRequest, LLMResult
from onyourtips.llm.models import Message, Usage
from onyourtips.llm.pricing import Pricing
from onyourtips.services.tokens import count_prompt, counter_for
from onyourtips.storage import Database

CHUNK_MARGIN_TOKENS = 200  # headroom so a chunk never brushes the context limit
NOTE_TOKENS_GUESS = 60  # per map note, for estimating the reduce prompt
NOTES_PER_CHUNK_GUESS = 4


class SummaryPlan(BaseModel):
    article_id: str
    content_hash: str | None
    prompt_version: str
    provider: str
    model: str
    title: str = ""
    tokenizer: str | None = None
    mode: str = "single"  # single | map_reduce
    chunks: list[list[int]] = []  # map-reduce: passage numbers per chunk (after the cap)
    total_chunks: int = 0  # before the cap
    partial: bool = False
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    num_ctx: int
    fits: bool  # can it be summarized at all (single pass or map-reduce)?
    reason: str | None = None  # why not
    messages: list[Message] = []  # single pass

    def request(self, **kw: Any) -> LLMRequest:
        return LLMRequest(
            purpose="summarize",
            messages=self.messages,
            model=self.model,
            prompt_version=self.prompt_version,
            article_id=self.article_id,
            max_tokens=self.estimated_output_tokens,
            num_ctx=self.num_ctx,
            **kw,
        )


def chunk_passages(
    passages: list[Passage], counter: TokenCounter, budget_tokens: int
) -> list[list[Passage]]:
    """Greedy: add passages to the current chunk until the next one would exceed the budget."""
    chunks: list[list[Passage]] = []
    current: list[Passage] = []
    used = 0
    for p in passages:
        cost = counter.count(f"[P{p.idx}] {p.text}") + 2  # + separator
        if current and used + cost > budget_tokens:
            chunks.append(current)
            current, used = [], 0
        current.append(p)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def _chunk_budget(settings: Settings, counter: TokenCounter) -> int:
    return (
        settings.llm_num_ctx
        - counter.count(MAP_SYSTEM)
        - settings.summary_map_output_tokens
        - TEMPLATE_OVERHEAD_TOKENS
        - CHUNK_MARGIN_TOKENS
    )


def plan_summary(
    settings: Settings, db: Database, article_id: str, *, model: str | None = None
) -> SummaryPlan:
    """Build the prompt, count its tokens, estimate the cost, pick single pass or map-reduce."""
    model = model or settings.llm_model
    provider = settings.llm_provider
    full_id = db.resolve_id(article_id)
    art = db.get_article(full_id)
    content = db.latest_content(full_id)
    base = dict(
        article_id=full_id,
        prompt_version=SUMMARY_PROMPT_VERSION,
        provider=provider,
        model=model,
        num_ctx=settings.llm_num_ctx,
        estimated_output_tokens=settings.summary_output_tokens,
        title=art.title if art else "",
    )
    if art is None or content is None or not content.usable or not content.content_hash:
        status = content.status.value if content else "not extracted"
        return SummaryPlan(
            content_hash=None, fits=False, reason=f"no usable text ({status})", **base
        )
    passages = db.get_passages(content.content_hash)
    if not passages:
        return SummaryPlan(
            content_hash=content.content_hash, fits=False, reason="no passages", **base
        )

    counter = counter_for(settings, db, model)
    pricing = Pricing.from_yaml(settings.pricing_file)
    user = summary_user_message(art.title, passages)
    _, _, input_tokens = count_prompt(counter, SUMMARY_SYSTEM, user)
    out = settings.summary_output_tokens

    if input_tokens + out <= settings.llm_num_ctx:
        cost, _ = pricing.cost(provider, model, Usage(input_tokens=input_tokens, output_tokens=out))
        return SummaryPlan(
            content_hash=content.content_hash,
            tokenizer=counter.name,
            estimated_input_tokens=input_tokens,
            estimated_cost_usd=cost,
            fits=True,
            messages=[
                Message(role="system", content=SUMMARY_SYSTEM),
                Message(role="user", content=user),
            ],
            **base,
        )

    # Map-reduce: estimate every map call plus the reduce call.
    groups = chunk_passages(passages, counter, _chunk_budget(settings, counter))
    kept = groups[: settings.summary_max_chunks]
    map_out = settings.summary_map_output_tokens
    est_in = sum(
        count_prompt(counter, MAP_SYSTEM, map_user_message(art.title, g, i, len(kept)))[2]
        for i, g in enumerate(kept, 1)
    )
    est_in += (
        counter.count(REDUCE_SYSTEM)
        + len(kept) * NOTES_PER_CHUNK_GUESS * NOTE_TOKENS_GUESS
        + TEMPLATE_OVERHEAD_TOKENS
    )
    est_out = len(kept) * map_out + out
    cost, _ = pricing.cost(provider, model, Usage(input_tokens=est_in, output_tokens=est_out))
    return SummaryPlan(
        content_hash=content.content_hash,
        tokenizer=counter.name,
        mode="map_reduce",
        chunks=[[p.idx for p in g] for g in kept],
        total_chunks=len(groups),
        partial=len(groups) > len(kept),
        estimated_input_tokens=est_in,
        estimated_cost_usd=cost,
        fits=True,
        **{**base, "estimated_output_tokens": est_out},
    )


# ----------------------------------------------------------------------------- run


class SummaryOutcome(BaseModel):
    article_id: str
    summary_id: int | None = None
    done: bool
    cached: bool = False
    mode: str | None = None
    description: str | None = None
    checks: SummaryChecks | None = None
    latency_ms: int = 0


def _request(
    plan: SummaryPlan, purpose: str, system: str, user: str, max_tokens: int, temperature: float
) -> LLMRequest:
    return LLMRequest(
        purpose=purpose,
        messages=[Message(role="system", content=system), Message(role="user", content=user)],
        model=plan.model,
        prompt_version=plan.prompt_version,
        article_id=plan.article_id,
        max_tokens=max_tokens,
        num_ctx=plan.num_ctx,
        temperature=temperature,
    )


def _map_reduce(
    settings: Settings,
    plan: SummaryPlan,
    passages: list[Passage],
    gw: LLMGateway,
    temperature: float,
) -> tuple[LLMResult, int]:
    """Returns the reduce call's result and how many chunks produced notes."""
    by_idx = {p.idx: p for p in passages}
    notes: list[KeyPoint] = []
    useful = 0
    for i, idxs in enumerate(plan.chunks, 1):
        chunk = [by_idx[x] for x in idxs]
        req = _request(
            plan,
            "summarize_map",
            MAP_SYSTEM,
            map_user_message(plan.title, chunk, i, len(plan.chunks)),
            settings.summary_map_output_tokens,
            temperature,
        )
        try:
            res = gw.complete(req, schema=MapNotes)
        except LLMError:
            continue  # one bad chunk shouldn't sink the whole summary; it's in the ledger
        allowed = set(idxs)
        # keep only citations inside this chunk: a note can't cite what the model didn't see
        for n in res.data.points:
            cites = [c for c in n.citations if c in allowed]
            if cites:
                notes.append(n.model_copy(update={"citations": cites}))
        useful += 1
    if not notes:
        raise LLMError("map-reduce: no chunk produced usable notes")
    req = _request(
        plan,
        "summarize_reduce",
        REDUCE_SYSTEM,
        reduce_user_message(plan.title, notes),
        settings.summary_output_tokens,
        temperature,
    )
    return gw.complete(req, schema=Summary), useful


def summarize_article(
    settings: Settings,
    db: Database,
    article_id: str,
    *,
    model: str | None = None,
    gateway: LLMGateway | None = None,
    trigger: str = "on_demand",
    force: bool = False,
    temperature: float = 0.2,
) -> SummaryOutcome:
    plan = plan_summary(settings, db, article_id, model=model)
    if not force and plan.content_hash:
        cached = db.latest_summary(plan.article_id, plan.model, plan.prompt_version)
        if cached:
            return SummaryOutcome(
                article_id=plan.article_id,
                summary_id=cached["id"],
                done=True,
                cached=True,
                mode=cached["mode"],
            )
    if not plan.fits:
        sid = record_summary(db, plan, error=plan.reason, trigger=trigger)
        return SummaryOutcome(
            article_id=plan.article_id, summary_id=sid, done=False, description=plan.reason
        )

    gw = gateway or LLMGateway(settings, db)
    passages = db.get_passages(plan.content_hash)
    before = db.max_llm_call_id()
    started = utcnow()
    chunks_used = None
    try:
        if plan.mode == "single":
            result = gw.complete(plan.request(temperature=temperature), schema=Summary)
        else:
            result, chunks_used = _map_reduce(settings, plan, passages, gw, temperature)
    except LLMError as exc:
        totals = db.llm_calls_after(before, plan.article_id, "summarize")
        sid = record_summary(db, plan, error=str(exc), totals=totals, trigger=trigger)
        return SummaryOutcome(
            article_id=plan.article_id,
            summary_id=sid,
            done=False,
            mode=plan.mode,
            description=str(exc),
        )

    summary, checks = check_summary(result.data, passages)
    totals = db.llm_calls_after(before, plan.article_id, "summarize")
    wall_ms = int((utcnow() - started).total_seconds() * 1000)
    sid = record_summary(
        db,
        plan,
        result=result,
        data=summary,
        checks=checks,
        totals=totals,
        error=None if checks.passed else checks.failure,
        chunks_used=chunks_used,
        trigger=trigger,
        wall_ms=wall_ms,
    )
    return SummaryOutcome(
        article_id=plan.article_id,
        summary_id=sid,
        done=checks.passed,
        mode=plan.mode,
        description=None if checks.passed else checks.failure,
        checks=checks,
        latency_ms=wall_ms,
    )


def record_summary(
    db: Database,
    plan: SummaryPlan,
    *,
    result: LLMResult | None = None,
    data: Any = None,
    error: str | None = None,
    call_id: int | None = None,
    checks: SummaryChecks | None = None,
    totals: dict | None = None,
    chunks_used: int | None = None,
    trigger: str | None = None,
    wall_ms: int | None = None,
) -> int:
    """One `summaries` row. Done = yes only with a result and no error; otherwise no + reason.

    Real tokens and cost come from `totals` (every ledger row of this attempt: map calls,
    retries, the reduce call) when given, else from the single result."""
    done = result is not None and error is None
    description = None if done else (error or plan.reason or "not sent")
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    real_in = real_out = real_cost = None
    calls = None
    if totals and totals.get("calls"):
        real_in, real_out = totals["input_tokens"], totals["output_tokens"]
        real_cost, calls = totals["cost_usd"], totals["calls"]
    elif result is not None:
        real_in, real_out, real_cost, calls = (
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.cost_usd,
            1,
        )
    elif call_id is not None:  # failed after reaching the model: it still had a cost
        row = db.conn.execute("SELECT cost_usd FROM llm_calls WHERE id=?", (call_id,)).fetchone()
        real_cost = row[0] if row else None
    return db.save_summary(
        {
            **plan.model_dump(
                include={
                    "article_id",
                    "content_hash",
                    "prompt_version",
                    "provider",
                    "model",
                    "tokenizer",
                    "estimated_input_tokens",
                    "estimated_output_tokens",
                    "estimated_cost_usd",
                    "mode",
                }
            ),
            "chunks": chunks_used if chunks_used is not None else (len(plan.chunks) or None),
            "partial": int(plan.partial),
            "calls": calls,
            "real_input_tokens": real_in,
            "real_output_tokens": real_out,
            "real_cost_usd": real_cost,
            "summarization_done": "yes" if done else "no",
            "description": description,
            "summary_json": json.dumps(data) if done and data is not None else None,
            "checks_json": checks.model_dump_json() if checks else None,
            "llm_call_id": result.call_id if result else call_id,
            "latency_ms": wall_ms
            if wall_ms is not None
            else (result.latency_ms if result else None),
            "trigger": trigger,
            "created_at": utcnow().isoformat(timespec="seconds"),
        }
    )


def list_summaries(db: Database, article_id: str | None = None, limit: int = 50) -> list[dict]:
    full = db.resolve_id(article_id) if article_id else None
    return db.list_summaries(full, limit)


# ----------------------------------------------------------------------------- view (API/CLI)


class CitedPassage(BaseModel):
    idx: int
    text: str


class SummaryView(BaseModel):
    article_id: str
    status: str  # ready | missing | failed
    summary_id: int | None = None
    tldr: str | None = None
    key_points: list[KeyPoint] = []
    why_it_matters: str | None = None
    passages: list[CitedPassage] = []  # the cited ones, for tap-to-read
    model: str | None = None
    mode: str | None = None
    partial: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int | None = None
    quote_match_rate: float | None = None
    created_at: str | None = None
    description: str | None = None  # why it failed


def get_summary_view(settings: Settings, db: Database, article_id: str) -> SummaryView:
    full = db.resolve_id(article_id)
    row = db.latest_summary(full, settings.llm_model, SUMMARY_PROMPT_VERSION) or db.latest_summary(
        full
    )
    if row is None:
        last = db.latest_summary(full, done_only=False)
        if last:
            return SummaryView(
                article_id=full,
                status="failed",
                description=last["description"],
                model=last["model"],
                created_at=last["created_at"],
            )
        return SummaryView(article_id=full, status="missing")
    s = Summary.model_validate_json(row["summary_json"])
    cited = sorted({c for kp in s.key_points for c in kp.citations})
    passages = [
        CitedPassage(idx=p.idx, text=p.text)
        for p in db.get_passages(row["content_hash"])
        if p.idx in cited
    ]
    checks = SummaryChecks.model_validate_json(row["checks_json"]) if row["checks_json"] else None
    return SummaryView(
        article_id=full,
        status="ready",
        summary_id=row["id"],
        tldr=s.tldr,
        key_points=s.key_points,
        why_it_matters=s.why_it_matters,
        passages=passages,
        model=row["model"],
        mode=row["mode"],
        partial=bool(row["partial"]),
        input_tokens=row["real_input_tokens"],
        output_tokens=row["real_output_tokens"],
        cost_usd=row["real_cost_usd"],
        latency_ms=row["latency_ms"],
        quote_match_rate=round(checks.quote_match_rate, 2) if checks else None,
        created_at=row["created_at"],
    )


# ----------------------------------------------------------------------------- cost report


class ModelProjection(BaseModel):
    provider: str
    model: str
    total_usd: float  # these summaries, priced on this model
    per_summary_usd: float
    per_month_usd: float  # at this window's daily rate


class SummaryReport(BaseModel):
    days: int
    model: str | None
    attempts: int
    done: int
    failed: int
    failure_reasons: dict[str, int]
    single: int
    map_reduce: int
    partial: int
    calls: int
    input_tokens: int
    output_tokens: int
    avg_input_tokens: int
    avg_output_tokens: int
    laptop_minutes: float  # time the model was busy (sum of call latencies)
    avg_seconds: float  # per successful summary, wall clock
    tokens_per_sec: float | None
    real_cost_usd: float
    estimate_error_pct: float | None  # |estimated - real| / real input tokens, single pass
    avg_quote_match: float | None
    avg_coverage: float | None
    repaired: int  # summaries where invalid citations were removed
    projections: list[ModelProjection] = []


def _reason_key(desc: str | None) -> str:
    d = (desc or "unknown").lower()
    for key in (
        "no usable text",
        "no passages",
        "didn't match",
        "no key point",
        "map-reduce",
        "timed out",
        "connect",
        "budget",
        "limit",
    ):
        if key in d:
            return key
    return d[:40]


def summary_report(
    settings: Settings, db: Database, days: int = 1, model: str | None = None
) -> SummaryReport:
    rows = db.summaries_since(utcnow() - timedelta(days=days), model)
    done = [r for r in rows if r["summarization_done"] == "yes"]
    failed = [r for r in rows if r["summarization_done"] == "no"]
    reasons: dict[str, int] = {}
    for r in failed:
        k = _reason_key(r["description"])
        reasons[k] = reasons.get(k, 0) + 1
    tin = sum(r["real_input_tokens"] or 0 for r in rows)
    tout = sum(r["real_output_tokens"] or 0 for r in rows)
    calls = sum(r["calls"] or 0 for r in rows)
    busy_ms = db.conn.execute(
        "SELECT COALESCE(SUM(latency_ms), 0) FROM llm_calls WHERE purpose LIKE 'summarize%' "
        "AND created_at >= ?" + (" AND model = ?" if model else ""),
        [(utcnow() - timedelta(days=days)).isoformat()] + ([model] if model else []),
    ).fetchone()[0]
    singles = [r for r in done if r["mode"] == "single" and r["real_input_tokens"]]
    err = (
        round(
            100
            * sum(
                abs(r["estimated_input_tokens"] - r["real_input_tokens"]) / r["real_input_tokens"]
                for r in singles
            )
            / len(singles),
            1,
        )
        if singles
        else None
    )
    checks = [SummaryChecks.model_validate_json(r["checks_json"]) for r in done if r["checks_json"]]
    with_quotes = [c for c in checks if c.quotes]
    lat = [r["latency_ms"] for r in done if r["latency_ms"]]
    rep = SummaryReport(
        days=days,
        model=model,
        attempts=len(rows),
        done=len(done),
        failed=len(failed),
        failure_reasons=dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        single=sum(1 for r in done if r["mode"] == "single"),
        map_reduce=sum(1 for r in done if r["mode"] == "map_reduce"),
        partial=sum(1 for r in done if r["partial"]),
        calls=calls,
        input_tokens=tin,
        output_tokens=tout,
        avg_input_tokens=round(tin / len(done)) if done else 0,
        avg_output_tokens=round(tout / len(done)) if done else 0,
        laptop_minutes=round(busy_ms / 60000, 1),
        avg_seconds=round(sum(lat) / len(lat) / 1000, 1) if lat else 0.0,
        tokens_per_sec=round(tout / (busy_ms / 1000), 1) if busy_ms else None,
        real_cost_usd=round(sum(r["real_cost_usd"] or 0 for r in rows), 6),
        estimate_error_pct=err,
        avg_quote_match=round(sum(c.quote_match_rate for c in with_quotes) / len(with_quotes), 3)
        if with_quotes
        else None,
        avg_coverage=round(sum(c.coverage for c in checks) / len(checks), 3) if checks else None,
        repaired=sum(1 for c in checks if c.invalid_citations_removed),
    )
    pricing = Pricing.from_yaml(settings.pricing_file)
    for provider, models in pricing.table.items():
        for name, price in models.items():
            if name == "*" or (price.input == 0 and price.output == 0):
                continue
            total = (tin * price.input + tout * price.output) / 1_000_000
            rep.projections.append(
                ModelProjection(
                    provider=provider,
                    model=name,
                    total_usd=round(total, 4),
                    per_summary_usd=round(total / len(done), 6) if done else 0.0,
                    per_month_usd=round(total / days * 30, 2),
                )
            )
    return rep
