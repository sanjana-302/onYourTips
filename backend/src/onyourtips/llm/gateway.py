"""The LLM gateway: the one door every AI call goes through (ADR 0003).

For each call it:
  1. checks budgets (daily/monthly spend, daily call limit),
  2. calls the provider, retrying transient failures with backoff,
  3. validates structured output against a pydantic schema (when given),
  4. writes one ledger row per attempt (ok, invalid or error) with tokens, cost and timings.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel, ValidationError

from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.llm.models import (
    BudgetExceeded,
    InvalidOutput,
    LLMCallRecord,
    LLMError,
    LLMRequest,
    LLMResult,
    Usage,
)
from onyourtips.llm.pricing import Pricing
from onyourtips.llm.providers import OllamaProvider, Provider
from onyourtips.storage import Database

log = logging.getLogger(__name__)


class Budget:
    def __init__(self, daily_usd: float, monthly_usd: float, daily_calls: int) -> None:
        self.daily_usd = daily_usd
        self.monthly_usd = monthly_usd
        self.daily_calls = daily_calls

    def check(self, db: Database, now: datetime) -> None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        today = db.llm_spend(since=day_start)
        if today["calls"] >= self.daily_calls:
            raise BudgetExceeded(
                f"Daily AI call limit reached ({self.daily_calls}). "
                "Raise OYT_LLM_DAILY_CALL_LIMIT if this is expected."
            )
        if today["cost_usd"] >= self.daily_usd:
            raise BudgetExceeded(
                f"Daily AI budget reached (${today['cost_usd']:.2f} of ${self.daily_usd:.2f})."
            )
        month = db.llm_spend(since=month_start)
        if month["cost_usd"] >= self.monthly_usd:
            raise BudgetExceeded(
                f"Monthly AI budget reached (${month['cost_usd']:.2f} of ${self.monthly_usd:.2f})."
            )


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        provider: Provider | None = None,
        pricing: Pricing | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.db = db
        self.provider = provider or build_provider(settings)
        self.pricing = pricing or Pricing.from_yaml(settings.pricing_file)
        self.budget = Budget(
            settings.llm_daily_budget_usd,
            settings.llm_monthly_budget_usd,
            settings.llm_daily_call_limit,
        )
        self.clock = clock
        self.sleep = sleep

    def complete(self, request: LLMRequest, schema: type[BaseModel] | None = None) -> LLMResult:
        """Run one request. With `schema`, the model is constrained to it and the output parsed."""
        model = request.model or self.settings.llm_model
        updates: dict = {}
        if request.num_ctx is None:
            updates["num_ctx"] = self.settings.llm_num_ctx
        if schema is not None and request.json_schema is None:
            updates["json_schema"] = schema.model_json_schema()
        if updates:
            request = request.model_copy(update=updates)

        self.budget.check(self.db, self.clock())

        attempts = self.settings.llm_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                resp = self.provider.chat(request, model)
            except LLMError as exc:
                exc.call_id = self._record(
                    request, model, attempt, started, status="error", error=str(exc)
                )
                if exc.retryable and attempt < attempts:
                    self._backoff(attempt, exc)
                    continue
                raise

            cost, pricing_version = self.pricing.cost(self.provider.name, resp.model, resp.usage)
            data, error = None, None
            if schema is not None:
                try:
                    data = schema.model_validate_json(resp.text)
                except ValidationError as ve:
                    error = f"output didn't match {schema.__name__}: {ve.errors()[0]['msg']}"
            status = "invalid" if error else "ok"
            call_id = self._record(
                request,
                resp.model,
                attempt,
                started,
                status=status,
                error=error,
                resp=resp,
                cost=cost,
                pricing_version=pricing_version,
            )
            if error:
                exc = InvalidOutput(error, call_id=call_id)
                if attempt < attempts:
                    self._backoff(attempt, exc)
                    continue
                raise exc
            return LLMResult(
                call_id=call_id,
                text=resp.text,
                data=data,
                provider=self.provider.name,
                model=resp.model,
                usage=resp.usage,
                cost_usd=cost,
                latency_ms=_elapsed_ms(started),
                tokens_per_sec=resp.tokens_per_sec,
                timings=resp.timings,
            )
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ helpers

    def _backoff(self, attempt: int, exc: LLMError) -> None:
        wait = self.settings.llm_backoff * 2 ** (attempt - 1)
        log.info("LLM attempt %d failed (%s); retrying in %.1fs", attempt, exc, wait)
        self.sleep(wait)

    def _record(
        self,
        request,
        model,
        attempt,
        started,
        *,
        status,
        error=None,
        resp=None,
        cost=0.0,
        pricing_version=None,
    ) -> int:
        record = LLMCallRecord(
            created_at=self.clock(),
            purpose=request.purpose,
            article_id=request.article_id,
            provider=self.provider.name,
            model=model,
            prompt_version=request.prompt_version,
            attempt=attempt,
            status=status,
            error=error,
            usage=resp.usage if resp else Usage(),
            cost_usd=cost,
            pricing_version=pricing_version,
            latency_ms=_elapsed_ms(started),
            tokens_per_sec=resp.tokens_per_sec if resp else None,
            timings=resp.timings if resp else {},
            request_id=resp.request_id if resp else None,
        )
        return self.db.record_llm_call(record)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def build_provider(settings: Settings) -> Provider:
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings.ollama_url, settings.llm_timeout, think=settings.llm_think)
    raise ValueError(f"unknown llm_provider '{settings.llm_provider}' (supported: ollama)")
