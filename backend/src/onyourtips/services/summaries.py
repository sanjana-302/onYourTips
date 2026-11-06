"""Recording summary attempts (ADR 0002 + ADR 0003): estimate before, real numbers after.

Summaries themselves aren't built yet. This is the bookkeeping the summarize feature will use:

    plan = plan_summary(settings, db, article_id)        # tokens + cost estimate, fits?
    if not plan.fits:
        record_summary(db, plan, error=plan.reason)      # done = no, never sent
    else:
        try:
            result = gateway.complete(plan.request(...), schema=...)
            record_summary(db, plan, result=result, data=result.data)   # done = yes
        except LLMError as exc:
            record_summary(db, plan, error=str(exc), call_id=exc.call_id)  # done = no

Every attempt gets a row in `summaries`, so estimated vs real cost can be compared per attempt,
and "too long for the context window" is caught *before* sending, instead of the model silently
cutting the article off.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.enrichment.summary_prompt import (
    SUMMARY_PROMPT_VERSION,
    SUMMARY_SYSTEM,
    summary_user_message,
)
from onyourtips.llm import LLMRequest, LLMResult
from onyourtips.llm.models import Message, Usage
from onyourtips.llm.pricing import Pricing
from onyourtips.services.tokens import count_prompt, counter_for
from onyourtips.storage import Database


class SummaryPlan(BaseModel):
    article_id: str
    content_hash: str | None
    prompt_version: str
    provider: str
    model: str
    tokenizer: str | None = None
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    num_ctx: int
    fits: bool
    reason: str | None = None  # why it can't be sent (no text, too long)
    messages: list[Message] = []

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


def plan_summary(
    settings: Settings, db: Database, article_id: str, *, model: str | None = None
) -> SummaryPlan:
    """Build the prompt, count its tokens, estimate the cost, and check it fits. No model call."""
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

    user = summary_user_message(art.title, passages)
    counter = counter_for(settings, db, model)
    _, _, input_tokens = count_prompt(counter, SUMMARY_SYSTEM, user)
    out = settings.summary_output_tokens
    cost, _ = Pricing.from_yaml(settings.pricing_file).cost(
        provider, model, Usage(input_tokens=input_tokens, output_tokens=out)
    )
    fits = input_tokens + out <= settings.llm_num_ctx
    reason = None
    if not fits:
        reason = (
            f"too long: {input_tokens:,} input + {out:,} output tokens > "
            f"{settings.llm_num_ctx:,}-token context window"
        )
    return SummaryPlan(
        content_hash=content.content_hash,
        tokenizer=counter.name,
        estimated_input_tokens=input_tokens,
        estimated_cost_usd=cost,
        fits=fits,
        reason=reason,
        messages=[
            Message(role="system", content=SUMMARY_SYSTEM),
            Message(role="user", content=user),
        ],
        **base,
    )


def record_summary(
    db: Database,
    plan: SummaryPlan,
    *,
    result: LLMResult | None = None,
    data: Any = None,
    error: str | None = None,
    call_id: int | None = None,
) -> int:
    """One `summaries` row. Done = yes only with a result and no error; otherwise no + reason."""
    done = result is not None and error is None
    description = None if done else (error or plan.reason or "not sent")
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    real_cost = None
    if result is not None:
        real_cost = result.cost_usd
    elif call_id is not None:  # the call failed after reaching the model: it still had a cost
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
                }
            ),
            "real_input_tokens": result.usage.input_tokens if result else None,
            "real_output_tokens": result.usage.output_tokens if result else None,
            "real_cost_usd": real_cost,
            "summarization_done": "yes" if done else "no",
            "description": description,
            "summary_json": json.dumps(data) if done and data is not None else None,
            "llm_call_id": result.call_id if result else call_id,
            "latency_ms": result.latency_ms if result else None,
            "created_at": utcnow().isoformat(timespec="seconds"),
        }
    )


def list_summaries(db: Database, article_id: str | None = None, limit: int = 50) -> list[dict]:
    full = db.resolve_id(article_id) if article_id else None
    return db.list_summaries(full, limit)
