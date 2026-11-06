"""Use cases around the LLM gateway: status, cost summaries and model comparisons."""

from __future__ import annotations

import platform
import re
from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.llm import (
    LLMError,
    LLMGateway,
    LLMRequest,
    ModelInfo,
    ProviderUnavailable,
    build_provider,
)
from onyourtips.storage import Database

# ----------------------------------------------------------------------------- status


class LLMStatus(BaseModel):
    provider: str
    url: str
    reachable: bool
    version: str | None = None
    default_model: str
    default_model_installed: bool = False
    models: list[ModelInfo] = []
    hint: str | None = None


def llm_status(settings: Settings) -> LLMStatus:
    provider = build_provider(settings)
    status = LLMStatus(
        provider=provider.name,
        url=settings.ollama_url,
        reachable=False,
        default_model=settings.llm_model,
    )
    try:
        status.models = provider.list_models()
    except ProviderUnavailable as exc:
        status.hint = str(exc)
        return status
    status.reachable = True
    status.version = provider.version()
    names = {m.name for m in status.models}
    # "llama3.2:3b" may be listed as-is; a bare name like "llama3.2" is listed as "llama3.2:latest".
    status.default_model_installed = (
        settings.llm_model in names or f"{settings.llm_model}:latest" in names
    )
    if not status.default_model_installed:
        status.hint = f"Default model isn't downloaded yet. Run: ollama pull {settings.llm_model}"
    return status


# ----------------------------------------------------------------------------- costs


class CostRow(BaseModel):
    key: str
    calls: int
    ok: int
    invalid: int
    errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    avg_latency_ms: int | None
    avg_tokens_per_sec: float | None


class CostSummary(BaseModel):
    days: int
    by: str
    total_calls: int
    total_cost_usd: float
    total_tokens: int
    rows: list[CostRow]


def cost_summary(db: Database, days: int = 7, by: str = "model") -> CostSummary:
    since = utcnow() - timedelta(days=days)
    rows = [
        CostRow(
            key=r["k"],
            calls=r["calls"],
            ok=r["ok"] or 0,
            invalid=r["invalid"] or 0,
            errors=r["errors"] or 0,
            input_tokens=r["input_tokens"] or 0,
            output_tokens=r["output_tokens"] or 0,
            cost_usd=r["cost_usd"] or 0.0,
            avg_latency_ms=r["avg_latency_ms"],
            avg_tokens_per_sec=r["avg_tokens_per_sec"],
        )
        for r in db.llm_summary(since, by=by)
    ]
    return CostSummary(
        days=days,
        by=by,
        total_calls=sum(r.calls for r in rows),
        total_cost_usd=round(sum(r.cost_usd for r in rows), 6),
        total_tokens=sum(r.input_tokens + r.output_tokens for r in rows),
        rows=rows,
    )


# ----------------------------------------------------------------------------- compare

SAMPLE_TEXT = (
    "Researchers released a 4-billion-parameter open-weight language model that matches much "
    "larger models on coding benchmarks. It was trained with reinforcement learning on "
    "verifiable rewards, runs on a laptop with 8 GB of memory, and ships under the Apache 2.0 "
    "licence. The team also published the training recipe and an evaluation harness so others "
    "can reproduce the results."
)

COMPARE_SYSTEM = (
    "You summarize technology news for a software engineer. Be accurate and concise. "
    "Only use facts from the text."
)
COMPARE_PROMPT_VERSION = "compare-v1"


class CompareSummary(BaseModel):
    """Structured output used by `oyt llm compare --structured`."""

    tldr: str = Field(description="One sentence")
    key_points: list[str] = Field(description="Three short bullet points")
    why_it_matters: str = Field(description="One sentence for a software engineer")


class CompareResult(BaseModel):
    model: str
    ok: bool
    text: str = ""
    error: str | None = None
    valid_json: bool | None = None  # only for structured runs
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    load_ms: float | None = None
    tokens_per_sec: float | None = None
    call_id: int | None = None


def compare_prompt(text: str, structured: bool) -> str:
    if structured:
        return f"Summarize this text.\n\nText:\n{text}"
    return (
        "Summarize this text in exactly 3 bullet points, then one line starting with "
        f"'Why it matters:'.\n\nText:\n{text}"
    )


def compare_models(
    settings: Settings,
    db: Database,
    models: list[str],
    text: str,
    *,
    structured: bool = False,
    article_id: str | None = None,
    gateway: LLMGateway | None = None,
) -> list[CompareResult]:
    """Run the same task on each model, one after another, with no retries.

    No retries on purpose: the comparison should show how reliable each model is on the
    first try (valid JSON, errors), not how reliable it is after the gateway helps.
    """
    no_retry = settings.model_copy(update={"llm_retries": 0})
    gw = gateway or LLMGateway(no_retry, db)
    gw.settings = no_retry
    results: list[CompareResult] = []
    for model in models:
        req = LLMRequest.simple(
            "compare",
            compare_prompt(text, structured),
            system=COMPARE_SYSTEM,
            model=model,
            prompt_version=COMPARE_PROMPT_VERSION,
            article_id=article_id,
            temperature=0.0,
        )
        try:
            res = gw.complete(req, schema=CompareSummary if structured else None)
            results.append(
                CompareResult(
                    model=model,
                    ok=True,
                    text=res.text,
                    valid_json=True if structured else None,
                    input_tokens=res.usage.input_tokens,
                    output_tokens=res.usage.output_tokens,
                    latency_ms=res.latency_ms,
                    load_ms=res.timings.get("load_ms"),
                    tokens_per_sec=res.tokens_per_sec,
                    call_id=res.call_id,
                )
            )
        except LLMError as exc:
            row = (
                db.conn.execute("SELECT * FROM llm_calls WHERE id=?", (exc.call_id,)).fetchone()
                if exc.call_id
                else None
            )
            results.append(
                CompareResult(
                    model=model,
                    ok=False,
                    error=str(exc),
                    valid_json=False if structured and row and row["status"] == "invalid" else None,
                    input_tokens=row["input_tokens"] if row else 0,
                    output_tokens=row["output_tokens"] if row else 0,
                    latency_ms=row["latency_ms"] if row else 0,
                    tokens_per_sec=row["tokens_per_sec"] if row else None,
                    call_id=exc.call_id,
                )
            )
    return results


def write_compare_report(
    out_dir: Path, results: list[CompareResult], text: str, *, structured: bool, title: str
) -> Path:
    """Save a Markdown report so you can look back at how models compared over time."""
    now = utcnow()
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "compare"
    path = out_dir / f"{now:%Y-%m-%d-%H%M}-{slug}.md"
    lines = [
        f"# Model comparison: {title}",
        "",
        f"- Date: {now:%Y-%m-%d %H:%M} UTC",
        f"- Machine: {platform.system()} {platform.machine()} "
        f"({platform.processor() or 'cpu n/a'})",
        f"- Task: {'structured JSON summary' if structured else 'free-text summary'} "
        f"({COMPARE_PROMPT_VERSION}), temperature 0, no retries",
        "",
        "| Model | Result | Total time | Load time | Tokens in / out | Speed | Valid JSON |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.model} | {'ok' if r.ok else 'failed'} | {r.latency_ms / 1000:.1f}s | "
            f"{(r.load_ms or 0) / 1000:.1f}s | {r.input_tokens} / {r.output_tokens} | "
            f"{f'{r.tokens_per_sec:.0f} tok/s' if r.tokens_per_sec else '-'} | "
            f"{'-' if r.valid_json is None else ('yes' if r.valid_json else 'no')} |"
        )
    lines += ["", "## Input", "", "> " + text.replace("\n", "\n> "), ""]
    for r in results:
        lines += [f"## {r.model}", ""]
        lines += [r.text.strip() if r.ok else f"**Failed:** {r.error}", ""]
    lines += [
        "## What to look at",
        "",
        "- **Faithfulness:** does every point come from the input, or did the model add facts?",
        "- **Instruction following:** exactly 3 points? valid JSON on the first try?",
        "- **Speed:** tokens per second (generation) vs load time (first call only).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
