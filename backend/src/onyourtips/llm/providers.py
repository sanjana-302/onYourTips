"""LLM providers. Only this module talks to model servers (ADR 0003).

A provider turns an LLMRequest into a ProviderResponse and raises LLMError subclasses for
failures. Retries, budgets, validation and the ledger are the gateway's job, not the provider's.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from onyourtips.llm.models import (
    LLMError,
    LLMRequest,
    ModelInfo,
    ModelNotFound,
    ProviderResponse,
    ProviderUnavailable,
    Usage,
)


class Provider(Protocol):
    name: str

    def chat(self, request: LLMRequest, model: str) -> ProviderResponse: ...

    def list_models(self) -> list[ModelInfo]: ...

    def version(self) -> str | None: ...


_NS_PER_MS = 1_000_000


class OllamaProvider:
    """Ollama's local HTTP API (https://github.com/ollama/ollama/blob/main/docs/api.md).

    POST /api/chat with stream=false returns the whole answer plus usage counters:
    prompt_eval_count (input tokens), eval_count (output tokens) and durations in nanoseconds.
    """

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 180.0,
        think: bool | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.think = think
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))

    # ------------------------------------------------------------------ chat

    def build_body(self, request: LLMRequest, model: str) -> dict:
        options: dict[str, float | int] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.num_ctx is not None:
            options["num_ctx"] = request.num_ctx
        body: dict = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
            "options": options,
        }
        if request.json_schema is not None:
            body["format"] = request.json_schema  # structured outputs: constrained decoding
        if self.think is not None:
            body["think"] = self.think
        return body

    def chat(self, request: LLMRequest, model: str) -> ProviderResponse:
        try:
            resp = self._client.post(
                f"{self.base_url}/api/chat", json=self.build_body(request, model)
            )
        except httpx.ConnectError:
            raise ProviderUnavailable(
                f"Can't reach Ollama at {self.base_url}. Start it with `ollama serve` "
                "(or open the Ollama app).",
                retryable=False,
            ) from None
        except httpx.TimeoutException:
            raise ProviderUnavailable(
                f"Ollama didn't answer in time ({model}). Large models are slow on first load; "
                "try again or raise OYT_LLM_TIMEOUT."
            ) from None
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Ollama request failed: {exc}") from None

        if resp.status_code != 200:
            detail = _error_detail(resp)
            if resp.status_code == 404 or "not found" in detail.lower():
                raise ModelNotFound(f"Model '{model}' isn't downloaded. Run: ollama pull {model}")
            if resp.status_code >= 500:
                raise ProviderUnavailable(f"Ollama error {resp.status_code}: {detail}")
            raise LLMError(f"Ollama rejected the request ({resp.status_code}): {detail}")

        data = resp.json()
        message = data.get("message") or {}
        timings = {
            key: round(data[src] / _NS_PER_MS, 1)
            for key, src in (
                ("total_ms", "total_duration"),
                ("load_ms", "load_duration"),
                ("prompt_ms", "prompt_eval_duration"),
                ("generation_ms", "eval_duration"),
            )
            if isinstance(data.get(src), (int, float))
        }
        out_tokens = int(data.get("eval_count") or 0)
        gen_ms = timings.get("generation_ms")
        return ProviderResponse(
            text=message.get("content") or "",
            # prompt_eval_count can be missing when Ollama reuses a cached prompt; then it's 0.
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0), output_tokens=out_tokens
            ),
            model=data.get("model") or model,
            timings=timings,
            tokens_per_sec=round(out_tokens / (gen_ms / 1000), 1)
            if gen_ms and out_tokens
            else None,
        )

    # ------------------------------------------------------------------ info

    def list_models(self) -> list[ModelInfo]:
        try:
            resp = self._client.get(f"{self.base_url}/api/tags", timeout=10.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            raise ProviderUnavailable(
                f"Can't reach Ollama at {self.base_url}. Start it with `ollama serve`.",
                retryable=False,
            ) from None
        models = []
        for m in resp.json().get("models", []):
            details = m.get("details") or {}
            models.append(
                ModelInfo(
                    name=m.get("name") or m.get("model", "?"),
                    size_bytes=m.get("size"),
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                    family=details.get("family"),
                    modified_at=m.get("modified_at"),
                )
            )
        return sorted(models, key=lambda m: m.name)

    def version(self) -> str | None:
        try:
            resp = self._client.get(f"{self.base_url}/api/version", timeout=5.0)
            resp.raise_for_status()
            return resp.json().get("version")
        except (httpx.HTTPError, ValueError):
            return None


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])
    except ValueError:
        pass
    return resp.text[:300] or resp.reason_phrase
