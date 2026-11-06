import json
from datetime import timedelta

import httpx
import pytest
from pydantic import BaseModel

from onyourtips.llm import (
    BudgetExceeded,
    InvalidOutput,
    LLMGateway,
    LLMRequest,
    ModelNotFound,
    OllamaProvider,
    Pricing,
    ProviderUnavailable,
)
from onyourtips.llm.models import LLMError, ProviderResponse, Usage
from onyourtips.llm.pricing import Price
from tests.helpers import NOW


class FakeProvider:
    """Scripted provider: each call pops the next response (or exception)."""

    name = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, request, model):
        self.calls.append((request, model))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def list_models(self):
        return []

    def version(self):
        return "test"


def ok(text="hello", inp=100, out=20, model="m1"):
    return ProviderResponse(
        text=text,
        usage=Usage(input_tokens=inp, output_tokens=out),
        model=model,
        timings={"generation_ms": 500.0},
        tokens_per_sec=40.0,
    )


@pytest.fixture
def make_gateway(settings, db):
    settings.llm_retries = 2
    settings.llm_model = "m1"
    pricing = Pricing("test-v1", {"fake": {"m1": Price(input=1.0, output=4.0), "*": Price()}})

    def make(script):
        provider = FakeProvider(script)
        gw = LLMGateway(
            settings,
            db,
            provider=provider,
            pricing=pricing,
            clock=lambda: NOW,
            sleep=lambda s: None,
        )
        return gw, provider

    return make


def ledger(db):
    return [dict(r) for r in db.conn.execute("SELECT * FROM llm_calls ORDER BY id")]


def test_success_records_tokens_cost_and_timings(make_gateway, db):
    gw, provider = make_gateway([ok()])
    res = gw.complete(LLMRequest.simple("ping", "hi", article_id="a1", prompt_version="p1"))
    assert res.text == "hello" and res.model == "m1"
    assert res.cost_usd == pytest.approx((100 * 1.0 + 20 * 4.0) / 1e6)
    assert res.tokens_per_sec == 40.0
    [row] = ledger(db)
    assert row["status"] == "ok" and row["purpose"] == "ping" and row["article_id"] == "a1"
    assert row["input_tokens"] == 100 and row["output_tokens"] == 20
    assert row["pricing_version"] == "test-v1" and row["prompt_version"] == "p1"
    assert json.loads(row["timings"]) == {"generation_ms": 500.0}
    # defaults applied by the gateway
    request, model = provider.calls[0]
    assert model == "m1" and request.num_ctx == gw.settings.llm_num_ctx


def test_transient_errors_are_retried_and_each_attempt_logged(make_gateway, db):
    gw, provider = make_gateway([ProviderUnavailable("busy"), ProviderUnavailable("busy"), ok()])
    res = gw.complete(LLMRequest.simple("ping", "hi"))
    assert res.text == "hello" and len(provider.calls) == 3
    assert [(r["attempt"], r["status"]) for r in ledger(db)] == [
        (1, "error"),
        (2, "error"),
        (3, "ok"),
    ]


def test_non_retryable_errors_fail_fast(make_gateway, db):
    gw, provider = make_gateway([ModelNotFound("pull it")])
    with pytest.raises(ModelNotFound) as exc:
        gw.complete(LLMRequest.simple("ping", "hi"))
    assert len(provider.calls) == 1 and exc.value.call_id == ledger(db)[0]["id"]


def test_gives_up_after_retries(make_gateway, db):
    gw, _ = make_gateway([ProviderUnavailable("down")] * 3)
    with pytest.raises(ProviderUnavailable):
        gw.complete(LLMRequest.simple("ping", "hi"))
    assert len(ledger(db)) == 3


class Summary(BaseModel):
    tldr: str
    points: list[str]


def test_structured_output_is_parsed_and_schema_sent(make_gateway):
    gw, provider = make_gateway([ok(json.dumps({"tldr": "t", "points": ["a", "b"]}))])
    res = gw.complete(LLMRequest.simple("summarize", "x"), schema=Summary)
    assert isinstance(res.data, Summary) and res.data.points == ["a", "b"]
    assert provider.calls[0][0].json_schema["title"] == "Summary"


def test_invalid_output_retried_then_raised(make_gateway, db):
    gw, _ = make_gateway([ok("not json"), ok('{"tldr": 1}'), ok("{}")])
    with pytest.raises(InvalidOutput):
        gw.complete(LLMRequest.simple("summarize", "x"), schema=Summary)
    assert [r["status"] for r in ledger(db)] == ["invalid"] * 3


def test_invalid_then_valid(make_gateway, db):
    gw, _ = make_gateway([ok("oops"), ok(json.dumps({"tldr": "t", "points": []}))])
    res = gw.complete(LLMRequest.simple("summarize", "x"), schema=Summary)
    assert res.data.tldr == "t"
    assert [r["status"] for r in ledger(db)] == ["invalid", "ok"]


def test_daily_call_limit(make_gateway, settings):
    settings.llm_daily_call_limit = 2
    gw, _ = make_gateway([ok(), ok(), ok()])
    gw.complete(LLMRequest.simple("ping", "1"))
    gw.complete(LLMRequest.simple("ping", "2"))
    with pytest.raises(BudgetExceeded, match="call limit"):
        gw.complete(LLMRequest.simple("ping", "3"))


def test_daily_budget(make_gateway, settings):
    settings.llm_daily_budget_usd = 0.0001
    gw, _ = make_gateway([ok(inp=1000, out=1000), ok()])
    gw.complete(LLMRequest.simple("ping", "1"))  # costs $0.005
    with pytest.raises(BudgetExceeded, match="Daily AI budget"):
        gw.complete(LLMRequest.simple("ping", "2"))


def test_unpriced_model_costs_zero(make_gateway, db, settings):
    settings.llm_model = "unknown-model"
    gw, _ = make_gateway([ok(model="unknown-model")])
    gw.pricing = Pricing("v", {})
    res = gw.complete(LLMRequest.simple("ping", "hi"))
    assert res.cost_usd == 0 and ledger(db)[0]["pricing_version"] == "unpriced"


def test_summary_by_model_purpose_day(make_gateway, db):
    gw, _ = make_gateway(
        [
            ok(),
            ok(model="m1"),
            ProviderUnavailable("x"),
            ProviderUnavailable("x"),
            ProviderUnavailable("x"),
        ]
    )
    gw.complete(LLMRequest.simple("ping", "1"))
    gw.complete(LLMRequest.simple("summarize", "2"))
    with pytest.raises(ProviderUnavailable):
        gw.complete(LLMRequest.simple("summarize", "3"))
    since = NOW - timedelta(days=1)
    [m] = db.llm_summary(since, by="model")
    assert m["k"] == "fake/m1" and m["calls"] == 5 and m["ok"] == 2 and m["errors"] == 3
    assert m["input_tokens"] == 200 and m["avg_tokens_per_sec"] == 40.0
    by_purpose = {r["k"]: r["calls"] for r in db.llm_summary(since, by="purpose")}
    assert by_purpose == {"ping": 1, "summarize": 4}
    assert db.llm_summary(since, by="day")[0]["k"] == NOW.date().isoformat()


def test_shipped_pricing_file_prices_ollama_at_zero(settings):
    p = Pricing.from_yaml(settings.pricing_file)
    cost, version = p.cost("ollama", "llama3.2:3b", Usage(input_tokens=10_000, output_tokens=5_000))
    assert cost == 0 and version != "unpriced"


# ----------------------------------------------------------------------------- Ollama provider


def ollama_with(handler):
    return OllamaProvider(
        "http://ollama.test", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_ollama_request_body_and_response_parsing():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "llama3.2:3b",
                "message": {"role": "assistant", "content": "Hi!"},
                "prompt_eval_count": 26,
                "eval_count": 120,
                "total_duration": 3_500_000_000,
                "load_duration": 900_000_000,
                "prompt_eval_duration": 100_000_000,
                "eval_duration": 2_400_000_000,
            },
        )

    p = ollama_with(handler)
    req = LLMRequest.simple(
        "ping",
        "hello",
        system="be brief",
        max_tokens=50,
        num_ctx=8192,
        json_schema={"type": "object"},
    )
    resp = p.chat(req, "llama3.2:3b")
    assert seen["path"] == "/api/chat"
    b = seen["body"]
    assert b["stream"] is False and b["model"] == "llama3.2:3b"
    assert b["messages"][0] == {"role": "system", "content": "be brief"}
    assert b["options"] == {"temperature": 0.2, "num_predict": 50, "num_ctx": 8192}
    assert b["format"] == {"type": "object"} and "think" not in b
    assert resp.text == "Hi!" and resp.usage.input_tokens == 26 and resp.usage.output_tokens == 120
    assert resp.timings == {
        "total_ms": 3500.0,
        "load_ms": 900.0,
        "prompt_ms": 100.0,
        "generation_ms": 2400.0,
    }
    assert resp.tokens_per_sec == 50.0


def test_ollama_think_flag_sent_when_configured():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "x"}, "eval_count": 1})

    p = OllamaProvider(
        "http://o", think=False, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    p.chat(LLMRequest.simple("ping", "x"), "qwen3.5:4b")
    assert seen["think"] is False


def test_ollama_model_not_found():
    p = ollama_with(lambda r: httpx.Response(404, json={"error": "model 'nope' not found"}))
    with pytest.raises(ModelNotFound, match="ollama pull nope"):
        p.chat(LLMRequest.simple("ping", "x"), "nope")


def test_ollama_server_error_is_retryable():
    p = ollama_with(lambda r: httpx.Response(500, json={"error": "out of memory"}))
    with pytest.raises(ProviderUnavailable, match="out of memory") as exc:
        p.chat(LLMRequest.simple("ping", "x"), "m")
    assert exc.value.retryable


def test_ollama_not_running():
    def handler(req):
        raise httpx.ConnectError("refused")

    p = ollama_with(handler)
    with pytest.raises(ProviderUnavailable, match="ollama serve") as exc:
        p.chat(LLMRequest.simple("ping", "x"), "m")
    assert exc.value.retryable is False
    with pytest.raises(ProviderUnavailable):
        p.list_models()


def test_ollama_bad_request_not_retryable():
    p = ollama_with(lambda r: httpx.Response(400, json={"error": "invalid format"}))
    with pytest.raises(LLMError) as exc:
        p.chat(LLMRequest.simple("ping", "x"), "m")
    assert not exc.value.retryable and "invalid format" in str(exc.value)


def test_ollama_list_models_and_version():
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3.5:4b",
                            "size": 3_400_000_000,
                            "details": {
                                "parameter_size": "4B",
                                "quantization_level": "Q4_K_M",
                                "family": "qwen3",
                            },
                        },
                        {
                            "name": "llama3.2:3b",
                            "size": 2_000_000_000,
                            "details": {"parameter_size": "3.2B"},
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"version": "0.12.0"})

    p = ollama_with(handler)
    models = p.list_models()
    assert [m.name for m in models] == ["llama3.2:3b", "qwen3.5:4b"]
    assert models[1].quantization == "Q4_K_M" and models[1].parameter_size == "4B"
    assert p.version() == "0.12.0"


def test_gateway_with_ollama_end_to_end(settings, db):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "model": "llama3.2:3b",
                "message": {"content": "ok"},
                "prompt_eval_count": 10,
                "eval_count": 5,
                "eval_duration": 100_000_000,
            },
        )

    gw = LLMGateway(settings, db, provider=ollama_with(handler), clock=lambda: NOW)
    res = gw.complete(LLMRequest.simple("ping", "hi"))
    assert res.provider == "ollama" and res.cost_usd == 0 and res.tokens_per_sec == 50.0
    assert ledger(db)[0]["provider"] == "ollama"
