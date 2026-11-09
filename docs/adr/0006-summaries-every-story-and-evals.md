# ADR 0006: Summarize every new story overnight, with citations, and evaluate in four layers

- **Status:** Accepted
- **Date:** 2026-11-09
- **Builds on:** ADR 0002 (summaries and citations), 0003 (cost ledger), 0004 (local models),
  0005 (daily pipeline)

## Context

ADR 0002 designed summaries with passage citations for *selected* stories. The product view is
different: you read in the morning, around 9 AM, and the refresh starts at 1 AM. That leaves
7–8 hours of otherwise idle laptop time, so every new story can be summarized before you wake up.
The cost still has to be measured. This feature is also the place to learn how AI output is
evaluated.

## Decision

### 1. Every new story, overnight, until 08:30
- **Nightly step:** after extraction, the nightly run summarizes every story first seen in the
  last 7 days whose current text has no summary for this model and prompt version.
  - **Order:** highest signal first, so the best stories are always done.
  - **Deadline:** it stops at `summary_deadline` (08:30 local time). Unfinished stories raise a
    `summaries_unfinished` alert and are done the next night or when opened.
- **Model check:** before starting, it checks the model is available. If it isn't, there's one
  `llm_unavailable` alert, not 500 failures.
- **Retries:** a story that fails twice for the same text, prompt and model isn't retried.
- **Refresh button:** it doesn't summarize, because it would spin for an hour. Opening a story
  without a summary offers **Summarize with AI**, which takes about 5–30 s.

### 2. Output contract (prompt `summary-v1`)
`{tldr, key_points: [{text, citations: [passage numbers], quote}], why_it_matters}`

The `quote` is a short verbatim phrase from a cited passage, ADR 0002's "stricter" option. It
makes each claim checkable by code, without a model. Topics come from the tagger, not the LLM.

### 3. Long stories: map-reduce
If the prompt doesn't fit the context window (8,192 tokens), the story is split:

- **Chunking:** passages are grouped greedily into chunks that fit.
- **Map:** each chunk produces up to 4 cited notes.
- **Reduce:** the notes are merged into the final summary.
- **Citation numbering:** passage numbers are global, so final citations point at real
  passages. Notes may only cite passages from their own chunk.
- **Cap:** 12 chunks. Longer stories are summarized from their first 12 chunks and marked
  `partial`.

### 4. Repair, gate, measure (deterministic checks)
- **Repair:** citations to passages that don't exist are removed.
- **Gate:** a key point left with no valid citation is dropped. A summary left with no key
  points fails (`summarization_done = no`, with the reason in `description`).
- **Measure:** these metrics are stored with every summary in `checks_json`. They're recorded,
  not gates:
  - quotes found (exact or fuzzy)
  - quotes found in the wrong passage
  - coverage
  - duplicates
  - repairs

### 5. One `summaries` row per attempt (from ADR 0003's ledger)
- **Estimated vs real:** tokens and cost, estimated vs real. Real numbers are the **sum of every
  ledger row** of the attempt: map calls, retries and the reduce call.
- **Bookkeeping:** `calls`, `mode`, `chunks`, `partial`, `trigger` and latency.
- **Cache key:** text hash + prompt version + model.
- **Report:** `oyt summaries` shows success, failure reasons, tokens, laptop minutes, estimate
  accuracy, and projected cost on any paid model in `pricing.yaml`.

### 6. Evals in four layers (`services/evals.py`, docs/evals-field-guide.md)

| Layer | What | Cost | Answers |
|---|---|---|---|
| 1. Deterministic checks | Code on every summary | Free | Is it well-formed and are claims traceable? |
| 2. Golden set + your labels | ~20 stratified stories, claim-level verdicts + 1–5 usefulness | Your time | What's actually true and useful? (ground truth) |
| 3. LLM-as-judge | A *different* model, verdict per claim against cited passages | Laptop time | Faithfulness at scale, once it agrees with you |
| 4. Model comparison | Same golden set × several models, scored by 1–3 | Laptop time | Which model to use? |

Trust in the judge is measured, not assumed: Cohen's kappa against your labels. Act on it
alone only at kappa ≥ 0.6.

## Consequences

- **Laptop time:** about 500 summaries a night at 5–15 s each is 1–2 hours. The deadline
  protects the morning.
- **Cost evidence:** every summary is cheap to audit: its passages, its quotes, its exact cost.
- **Learning:** the eval tables (`eval_golden`, `claim_judgments`, `summary_ratings`) make
  quality measurable across prompt and model versions.
- **Not done yet:**
  - a dev/test split of the golden set (to avoid tuning on the test set)
  - evals in CI on recorded outputs
  - judging every nightly summary automatically

## Alternatives considered

- **Summarize on demand only:** cheapest, but every first open waits 5–30 s, and there's no
  overnight data for evals and cost.
- **Truncate long stories:** simpler, but silently drops the end of exactly the stories where a
  summary helps most. Map-reduce keeps citations valid across the whole text.
- **Reject any summary with an invalid citation:** stricter, but small models would fail often.
  Repair-and-measure keeps the summary usable and makes the problem visible as a metric.
- **Let the summarizer judge itself:** convenient, but models favour their own output
  (self-preference bias). The judge must be a different model, and the CLI warns when it isn't.
