# Architecture Decision Records

Short notes recording a significant decision: context, decision, alternatives, consequences.
Add a new one when a choice would be hard to reverse or would puzzle a future reader.
Copy the format of an existing ADR and give it the next number. ADRs are never rewritten; a later
ADR supersedes an earlier one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-repo-structure-and-storage.md) | Monorepo, one backend package, SQLite + versioned migrations | Accepted |
| [0002](0002-summaries-and-citations.md) | LLM summaries with passage-level citations | Accepted, built (scope widened by 0006) |
| [0003](0003-llm-cost-ledger.md) | LLM gateway + cost ledger, cost visible from day one | Accepted, built |
| [0004](0004-local-models-first.md) | Start with local open models (Ollama) behind the same gateway | Accepted, built |
| [0005](0005-daily-refresh-pipeline.md) | Daily 1 AM refresh; full text for every new story; retry twice, then alert | Accepted, built |
| [0006](0006-summaries-every-story-and-evals.md) | Summarize every new story overnight with citations; map-reduce; four eval layers | Accepted, built |
