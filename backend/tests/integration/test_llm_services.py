import json

from onyourtips.llm import LLMGateway, ModelNotFound, Pricing
from onyourtips.llm.models import ProviderResponse, Usage
from onyourtips.services.llm import (
    CompareSummary,
    compare_models,
    cost_summary,
    llm_status,
    write_compare_report,
)
from tests.helpers import NOW


class ByModel:
    """Fake provider whose answer depends on the model name."""

    name = "fake"

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def chat(self, request, model):
        self.calls.append((request, model))
        a = self.answers[model]
        if isinstance(a, Exception):
            raise a
        return ProviderResponse(
            text=a,
            usage=Usage(input_tokens=50, output_tokens=30),
            model=model,
            timings={"load_ms": 1200.0, "generation_ms": 600.0},
            tokens_per_sec=50.0,
        )

    def list_models(self):
        return []

    def version(self):
        return None


VALID = json.dumps({"tldr": "t", "key_points": ["a", "b", "c"], "why_it_matters": "w"})


def gateway(settings, db, provider):
    return LLMGateway(
        settings,
        db,
        provider=provider,
        pricing=Pricing("v", {}),
        clock=lambda: NOW,
        sleep=lambda s: None,
    )


def test_compare_structured_measures_first_try_validity(settings, db):
    settings.llm_retries = 3  # compare must ignore this and not retry
    provider = ByModel(
        {"good": VALID, "sloppy": "Sure! Here is JSON: {", "absent": ModelNotFound("pull")}
    )
    results = compare_models(
        settings,
        db,
        ["good", "sloppy", "absent"],
        "Some text",
        structured=True,
        gateway=gateway(settings, db, provider),
    )
    by = {r.model: r for r in results}
    assert by["good"].ok and by["good"].valid_json is True and by["good"].load_ms == 1200.0
    assert not by["sloppy"].ok and by["sloppy"].valid_json is False
    assert by["sloppy"].output_tokens == 30  # usage still recorded for invalid output
    assert not by["absent"].ok and by["absent"].valid_json is None and "pull" in by["absent"].error
    # exactly one call per model: no retries during comparisons
    assert [m for _, m in provider.calls] == ["good", "sloppy", "absent"]
    req = provider.calls[0][0]
    assert req.temperature == 0.0 and req.purpose == "compare"
    assert req.json_schema["title"] == CompareSummary.__name__
    assert settings.llm_retries == 3  # caller's settings untouched


def test_compare_free_text(settings, db):
    provider = ByModel({"m": "- one\n- two\n- three\nWhy it matters: x"})
    [r] = compare_models(settings, db, ["m"], "Text", gateway=gateway(settings, db, provider))
    assert r.ok and r.valid_json is None and r.text.startswith("- one")
    assert "3 bullet points" in provider.calls[0][0].messages[-1].content


def test_compare_report(tmp_path, settings, db):
    provider = ByModel({"good": VALID, "bad": ModelNotFound("Run: ollama pull bad")})
    results = compare_models(
        settings,
        db,
        ["good", "bad"],
        "Input text here",
        structured=True,
        gateway=gateway(settings, db, provider),
    )
    path = write_compare_report(
        tmp_path, results, "Input text here", structured=True, title="GPT-6 launch!"
    )
    md = path.read_text()
    assert path.name.endswith("-gpt-6-launch.md")
    assert "| good | ok |" in md and "| bad | failed |" in md and "| yes |" in md
    assert "> Input text here" in md and "ollama pull bad" in md


def test_cost_summary_totals(settings, db):
    provider = ByModel({"a": "x", "b": "y"})
    gw = gateway(settings, db, provider)
    compare_models(settings, db, ["a", "b"], "t", gateway=gw)
    # cost_summary looks back from "now"; the fake clock is NOW (in the past), so use a wide window
    s = cost_summary(db, days=3650, by="model")
    assert s.total_calls == 2 and s.total_tokens == 160 and s.total_cost_usd == 0
    assert {r.key for r in s.rows} == {"fake/a", "fake/b"}


def test_llm_status_when_ollama_not_running(settings):
    settings.ollama_url = "http://127.0.0.1:9"  # nothing listens here
    st = llm_status(settings)
    assert not st.reachable and "ollama serve" in st.hint and st.default_model == settings.llm_model
