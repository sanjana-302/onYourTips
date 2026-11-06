"""Provider-neutral request/response types for the LLM gateway."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    """What a feature asks for. The gateway fills in defaults (model, context size)."""

    purpose: str  # what the call is for: summarize, compare, ping, ... (shows up in the ledger)
    messages: list[Message]
    model: str | None = None  # None = settings.llm_model
    prompt_version: str | None = None
    article_id: str | None = None
    json_schema: dict[str, Any] | None = None  # constrain output to this JSON schema
    temperature: float | None = 0.2
    max_tokens: int | None = None
    num_ctx: int | None = None  # context window (Ollama); None = settings.llm_num_ctx

    @classmethod
    def simple(cls, purpose: str, prompt: str, system: str | None = None, **kw: Any) -> LLMRequest:
        msgs = [Message(role="system", content=system)] if system else []
        msgs.append(Message(role="user", content=prompt))
        return cls(purpose=purpose, messages=msgs, **kw)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ProviderResponse(BaseModel):
    """What a provider returns: raw text plus usage and timing data."""

    text: str
    usage: Usage
    model: str
    request_id: str | None = None
    # Provider-specific timing breakdown in ms (Ollama: load_ms, prompt_ms, generation_ms, total_ms)
    timings: dict[str, float] = Field(default_factory=dict)
    tokens_per_sec: float | None = None


class LLMResult(BaseModel):
    """What a feature gets back from the gateway."""

    call_id: int  # row in the llm_calls ledger
    text: str
    data: Any | None = None  # parsed and validated output when a schema was given
    provider: str
    model: str
    usage: Usage
    cost_usd: float
    latency_ms: int
    tokens_per_sec: float | None = None
    timings: dict[str, float] = Field(default_factory=dict)


class LLMCallRecord(BaseModel):
    """One row of the llm_calls ledger."""

    created_at: datetime
    purpose: str
    article_id: str | None = None
    provider: str
    model: str
    prompt_version: str | None = None
    attempt: int = 1
    status: Literal["ok", "invalid", "error"]
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    pricing_version: str | None = None
    latency_ms: int = 0
    tokens_per_sec: float | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    request_id: str | None = None


class ModelInfo(BaseModel):
    name: str
    size_bytes: int | None = None
    parameter_size: str | None = None  # e.g. "3.2B"
    quantization: str | None = None  # e.g. "Q4_K_M"
    family: str | None = None
    modified_at: str | None = None


# ----------------------------------------------------------------------------- errors


class LLMError(Exception):
    """Base error. `retryable` tells the gateway whether trying again might help."""

    retryable = False

    def __init__(self, message: str, *, retryable: bool | None = None, call_id: int | None = None):
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable
        self.call_id = call_id


class ProviderUnavailable(LLMError):
    """Provider unreachable, overloaded or timed out."""

    retryable = True


class ModelNotFound(LLMError):
    """The requested model isn't available (for Ollama: not pulled yet)."""


class BudgetExceeded(LLMError):
    """A daily/monthly budget or call limit would be exceeded. Never retried."""


class InvalidOutput(LLMError):
    """The model answered, but the output didn't match the requested schema."""

    retryable = True
