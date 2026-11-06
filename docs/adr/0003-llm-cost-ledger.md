# ADR 0003 — Every LLM call goes through one gateway and is recorded in a cost ledger

- **Status:** Accepted (implementation pending)
- **Date:** 2026-11-01

## Context

The cost of each summary should be visible from the very first version: shown in the UI,
queryable, and trustworthy. Later work will reduce it (caching, batching, smaller models), and
those improvements are only worth claiming if there is a measured baseline to compare against.

## Decision

### One gateway

`onyourtips.llm.gateway` is the only module that imports a provider SDK. It handles retries,
timeouts, the budget guard and recording calls. Everything else (`enrichment`, `services`) calls
the gateway with a `purpose` such as `"summarize"`.

### Ledger table: `llm_calls`

| column | notes |
|---|---|
| `id`, `created_at`, `request_id` | provider request id, for debugging |
| `purpose`, `article_id` | what the money was spent on |
| `provider`, `model`, `prompt_version` | |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` | **from the API response's usage data, never estimated** |
| `cost_usd` | calculated and **stored** when the call is made |
| `pricing_version` | which price entry was used |
| `latency_ms`, `status`, `error`, `attempt` | failed calls and retries are logged too — they cost money |

### Prices in config, not code

`backend/config/pricing.yaml` lists the per-million-token rate for input, output, cache read and
cache write for each model, with an `effective_from` date, plus any batch discount. Prices come from
the provider's pricing page and are updated by hand. Cost is calculated and stored when each call is
made, so a later price change never rewrites history.

### Before the call

- **Estimate:** count input tokens (provider token-count endpoint, or ~4 chars/token as a fallback)
  and show "≈ $0.004" before generating.
- **Budget guard:** `OYT_LLM_DAILY_BUDGET_USD` and `OYT_LLM_MONTHLY_BUDGET_USD`. The gateway refuses
  calls that would exceed them and the UI shows why.

### Where cost is shown

- Under every summary: `model · 3.1k in / 280 out · $0.0042 · 2.3s`.
- `oyt costs` and `GET /costs`: by day, model and purpose; cost per digest.
- **North-star metric: cost per useful summary** = total summarization spend ÷ summaries that were
  opened or saved (from the interactions log). It captures both price and waste.

### Baseline, then optimize (later ADRs)

Each lever gets its own ADR with before/after numbers from the ledger:
- trim and clean the extracted input
- prompt caching for the fixed system prompt
- batch API for digest summaries
- a cheaper model for short or simple articles
- skip articles below the quality threshold
- dedupe by `content_hash`

## Alternatives considered

- **Logging cost only to stdout or an observability tool:** easy to lose and hard to join with
  articles and interactions. The ledger lives next to the data it explains.
- **Estimating cost from text length:** off by 10–30%, and it misses cache and retry effects. API usage data is exact.
- **Computing cost at query time from current prices:** history would change whenever prices
  change. Rejected.

## Consequences

- Every summary has an exact, auditable cost from day one.
- Changing provider or adding a model only touches `llm/` and `pricing.yaml`.
- Small overhead: one insert per call, and `pricing.yaml` needs manual updates when prices change.
