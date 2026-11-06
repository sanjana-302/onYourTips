# ADR 0004: Start with local open models (Ollama), behind the same gateway

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

OnYourTips is a learning project. The plan is to build the AI features one at a time
(see `docs/OnYourTips-AI-Roadmap.xlsx`) and to see how models differ and improve, not only
to ship features. Paid APIs cost money for every experiment, and they hide much of what
happens (load time, generation speed, context limits).

## Decision

1. **The first provider is Ollama**, which runs open-weight models locally over HTTP
   (`POST /api/chat`). The default model is `llama3.2:3b`: about 2 GB, fast on a laptop, and a
   well-known baseline. Override it with `OYT_LLM_MODEL`.
2. **The gateway from ADR 0003 is unchanged.** Ollama is one `Provider` implementation in
   `llm/providers.py`. Adding a paid provider later means one more class plus entries in
   `config/pricing.yaml`. No feature code changes.
3. **The ledger still records every call.** Local calls cost $0 (`ollama: "*": 0` in
   `pricing.yaml`), but tokens, latency, model load time and generation speed (tokens/second)
   are stored. On a laptop, those are the real costs.
4. **Context size is set per request** (`OYT_LLM_NUM_CTX`, default 8192). Ollama's default is
   4096 tokens, which silently truncates long articles.
5. **Comparing models is a first-class tool.** `oyt llm compare -m A -m B` runs the same
   summarization task on each model at temperature 0 with **no retries**, and saves a Markdown
   report in `backend/data/compare/`. Running it again as new models come out builds a record
   of how they improve on your own content and hardware.
6. **The daily call limit is the main guard** (`OYT_LLM_DAILY_CALL_LIMIT`, default 2000).
   Money budgets stay in place for when a paid provider is added.

## What to compare between models

| Signal | Where it shows | Why it matters |
|---|---|---|
| Valid JSON on the first try | `compare --structured`, ledger `status = invalid` | Structured output is what later features rely on |
| Faithfulness | Report outputs vs the input text | Small models add facts that aren't in the text |
| Instruction following | "exactly 3 bullet points" | Predictable output means simpler code |
| Generation speed | `tokens_per_sec` in the ledger | Decides what can run interactively |
| Load time | `timings.load_ms` | Cold starts dominate the first call |

## Alternatives considered

- **Paid API first (Claude, OpenAI):** better quality and built-in citations, but every
  experiment costs money, and it teaches less about how models behave. Planned as a second
  provider, so the same comparisons can include it.
- **llama.cpp or vLLM directly:** more control, more setup. Ollama wraps llama.cpp with model
  management and a stable HTTP API, which is enough for now.
- **Calling Ollama from each feature:** quick, but each feature would reinvent retries, logging
  and limits. Rejected (see ADR 0003).

## Consequences

- No API keys or bills, and experiments are free.
- Quality, especially faithfulness and JSON reliability on small models, will be lower than
  frontier APIs. Summaries (feature #3) must validate output and handle failures gracefully.
- Performance depends on the Mac (RAM, Apple silicon GPU). Model load on the first call can
  take several seconds.
- Ollama must be running (`ollama serve` or the Ollama app). `oyt llm status` checks it.
