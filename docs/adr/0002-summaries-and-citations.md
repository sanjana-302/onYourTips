# ADR 0002 — LLM summaries with passage-level citations

- **Status:** Accepted (implementation pending)
- **Date:** 2026-11-01

## Context

The app's first view for a selected article will be an LLM-written summary. To be trustworthy
it must cite where each claim comes from, so I can check it against the source with one click.
Summaries also have to be reproducible (which model and prompt produced this?), cheap to keep
(never pay twice for the same input), and comparable across prompt and model versions.

## Decision

### Three stored layers

```
articles ─► article_content ─► passages
                          └──► summaries
```

**`article_content`**: extracted full text (e.g. `trafilatura`), one row per extraction attempt.

| column | notes |
|---|---|
| `article_id` | FK |
| `content_hash` | sha256 of the normalized text — the identity of "this input" |
| `extractor`, `extractor_version` | so improved extraction can be re-run selectively |
| `text`, `word_count` | |
| `status`, `error` | `ok` / `paywalled` / `failed` / `too_short` |
| `fetched_at` | |

Kept separate from `articles` because it's large, can fail, and gets re-extracted.

**`passages`**: the extracted text split into numbered paragraphs. These are what citations point to.

| column | notes |
|---|---|
| `content_hash`, `idx` | PK. idx = 1..N in reading order |
| `text`, `char_start`, `char_end` | offsets into `article_content.text` for highlighting |

**`summaries`**: one row per generation.

| column | notes |
|---|---|
| `article_id`, `content_hash`, `prompt_version`, `model` | **UNIQUE** — acts as a cache key |
| `schema_version`, `summary_json` | validated by a pydantic model |
| `status` | `ok` / `invalid` (failed validation) / `error` |
| `llm_call_id` | FK to the cost ledger (ADR 0003) |
| `created_at` | |

The "current" summary for an article = the latest `ok` row for its current `content_hash` and the
active `prompt_version`. Older rows are kept for comparison and evals.

### Output contract

The model gets numbered passages (`[P1] … [P2] …`) and must return JSON:

```json
{
  "tldr": "One-sentence takeaway.",
  "key_points": [
    {"text": "Model X beats the previous best on SWE-bench by 4 points.", "citations": [3, 7]},
    {"text": "Weights are released under Apache 2.0.", "citations": [11]}
  ],
  "why_it_matters": "…",
  "topics": ["llms", "ai-coding"]
}
```

### Validation before saving

1. JSON parses into the pydantic schema.
2. Every key point has ≥1 citation, and every cited passage exists.
3. *(Optional, stricter)* each key point includes a short quote that must appear in the cited passage.

Failures are stored as `status=invalid` and never shown. **Citation-validity rate** is the first
quality metric; faithfulness (LLM-as-judge on a small golden set) comes later in `tests/evals/`.

### Provider citations

The Claude API has a built-in citations feature: the article is passed as a document and responses
include the cited text with character positions. When using Claude, those positions are mapped onto
`passages` rows. The stored format stays provider-neutral, so models and providers can be swapped.

### Which articles get summarized

Only **selected** ones: digest picks, saved articles, or on demand when opened in the app. Never all
~600 items per ingest run. This is the first cost control (ADR 0003).

### Two citation levels in the UI

- In-article: each key point links to and highlights its passages.
- Source: canonical URL plus every sighting's discussion URL (HN, Reddit, …), already stored.

## Alternatives considered

- **Summary as plain text in `articles.summary`:** no citations, no versioning, overwritten on
  every run. Rejected.
- **Citations as free-text URLs/quotes from the model:** unverifiable and prone to hallucinated
  quotes. Rejected in favour of IDs that can be validated.
- **Sentence-level passages:** finer highlights but many more IDs and tokens. Paragraphs first;
  revisit if citations feel too coarse.

## Consequences

- Prompt changes are safe: a new `prompt_version` produces new rows, and old and new can be compared.
- Re-ingesting an unchanged article costs nothing (same `content_hash` → cache hit).
- Extraction quality now caps summary quality; paywalled/JS-only pages fall back to the feed's
  description and are flagged as "summary from excerpt".
