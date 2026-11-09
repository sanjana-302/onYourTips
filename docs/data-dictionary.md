# Data dictionary

Every table and column in `backend/data/onyourtips.db`: what it means, which code writes it, and
how tables connect. The schema comes from the numbered migrations in
`backend/src/onyourtips/storage/migrations/` (currently 0001–0009).

**Keeping it current:** `tests/unit/test_data_dictionary.py` fails if a table or column exists in
the migrations but not here. A new migration means a new row in this file.

**Conventions:**
- **Times** are ISO-8601 text in UTC (`2026-11-09T01:00:04+00:00`).
- **Booleans** are `INTEGER` 0/1.
- **JSON** is stored as `TEXT`; read it with SQLite's `json_extract(column, '$.field')`.
- **Article ids** are the first 16 hex characters of sha256(canonical URL). Commands accept a
  prefix, like `3f9a1c2e`.

## How the tables connect

```
                         ┌──────────── sightings (who saw it, points, comments)
                         ├──────────── interactions (your open / save / dismiss / rate)
articles ────────────────┼──────────── article_content ──(content_hash)──► passages
  (one per canonical URL)│                 (full text, one row per attempt)
                         ├──────────── token_counts (summary-size estimates and measurements)
                         ├──────────── summaries ──┬──► claim_judgments (you and LLM judges)
                         │                         └──► summary_ratings (your 1–5)
                         ├──────────── eval_golden (stories picked for evals)
                         └──────────── llm_calls (cost ledger; also judge and compare calls)

ingestion_runs (one row per source per ingest) ─┐
pipeline_runs  (one row per nightly / manual run)├─ operations: what ran, what failed
alerts         (things a developer should check) ┘
```

| Group | Tables |
|---|---|
| Content | `articles`, `sightings`, `article_content`, `passages` |
| You | `interactions` |
| AI and cost | `llm_calls`, `token_counts`, `summaries` |
| Evals | `eval_golden`, `claim_judgments`, `summary_ratings` |
| Operations | `ingestion_runs`, `pipeline_runs`, `alerts` |

---

## Content

### articles
One row per story, **unique per canonical URL**. The same story seen on HN and Reddit is one
article with two sightings. Written by `ingestion/pipeline.py` (ingest) and `services/nightly.py`
(retag). Migration 0001.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT PK | sha256(canonical_url)[:16] |
| `canonical_url` | TEXT unique | Normalized URL: https, no `www`, no tracking parameters, one arXiv version |
| `url` | TEXT | The URL as the source gave it |
| `title` | TEXT | Cleaned title |
| `title_fp` | TEXT | Title fingerprint: catches the same story under different URLs within ±3 days |
| `summary` | TEXT | The source's own description (feed blurb), not the AI summary |
| `author` | TEXT | Author or submitter, when known |
| `content_type` | TEXT | `news` · `project` · `research_paper` · `blog_post` · `discussion` (inferred from the URL and source) |
| `published_at` | TEXT | When the source says it was published (can be NULL) |
| `first_seen_at` | TEXT | When OnYourTips first saw it. Drives the 7-day windows |
| `last_seen_at` | TEXT | Most recent sighting |
| `topics` | TEXT JSON list | Topic ids from the keyword tagger (`config/taxonomy.yaml`), e.g. `["llms","agents"]` |
| `source_tags` | TEXT JSON list | Tags the source itself gave (subreddit flair, dev.to tags, …) |
| `primary_source_id` | TEXT | Source that first brought it in (id from `config/sources.yaml`) |
| `signal` | REAL | Engagement score: Σ source weight × (1 + log1p(points + 1.5·comments + 0.5·stars)) |
| `extra` | TEXT JSON | Source-specific extras (GitHub language, arXiv categories, …) |

### sightings
"Source X saw article Y with N points": one row per article per source. Written at ingest; points
and comments are updated each time the story is seen again. Migration 0001.

| Column | Type | Meaning |
|---|---|---|
| `article_id` | TEXT PK, FK articles | |
| `source_id` | TEXT PK | Source id from `sources.yaml` (`hn-front`, `reddit-ml`, …) |
| `external_id` | TEXT | The source's own id (HN item id, Reddit post id, …) |
| `discussion_url` | TEXT | Link to the comments (HN thread, Reddit post) |
| `points` | INTEGER | Upvotes or score at the last sighting |
| `comments` | INTEGER | Comment count at the last sighting |
| `stars` | INTEGER | GitHub stars (projects) |
| `seen_at` | TEXT | Last time this source showed it |

### article_content
Extracted full text, **one row per extraction attempt** (failures included), so the latest row is
the current state. Written by `services/content.py` / `enrichment/extraction.py`. Migrations
0004, 0005, 0006.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | Higher = newer attempt. "Current" = `MAX(id)` per article |
| `article_id` | TEXT FK articles | |
| `content_hash` | TEXT | sha256 of the normalized text: the identity of this input. Passages, token counts and summaries key on it, so unchanged text never costs twice |
| `extractor` | TEXT | `trafilatura`, `html-fallback`, `github-readme`, or `feed` (used the feed's description) |
| `extractor_version` | TEXT | Version of the extractor, so better extraction can be re-run selectively |
| `source_url` | TEXT | What was fetched |
| `text` | TEXT | Normalized full text (NULL when failed) |
| `word_count` | INTEGER | Words in `text` |
| `status` | TEXT | `ok` (full text) · `excerpt` (feed description only: blocked, too short) · `skipped` (PDF, video, robots.txt) · `failed` |
| `error` | TEXT | Why it failed or fell back |
| `fetched_at` | TEXT | When |
| `retryable` | INTEGER 0/1 | 1 = temporary failure (timeout, 5xx, 429): retried next night, given up after 2 |
| `counts_as_attempt` | INTEGER 0/1 | 0 = failed while the network was down: doesn't use up one of the 2 attempts |

### passages
The extracted text split into numbered paragraphs: **what citations point to**. Keyed by
`content_hash`, not article, so identical text shares passages. Written with `article_content`.
Migration 0004.

| Column | Type | Meaning |
|---|---|---|
| `content_hash` | TEXT PK | Links to `article_content.content_hash` |
| `idx` | INTEGER PK | 1..N in reading order. **P3 in a summary = `idx` 3** |
| `text` | TEXT | The paragraph (short headings merged in; long paragraphs split at sentences, ≤ 1,200 chars) |
| `char_start` | INTEGER | Start offset in `article_content.text`, for highlighting |
| `char_end` | INTEGER | End offset |

---

## You

### interactions
**Append-only** log of what you did. Current state (saved? dismissed?) is computed by replaying it
(`ArticleState`). Feeds `oyt interests` and, later, recommendations. Written by
`services/articles.py` (app and CLI). Migration 0002.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | Order of events |
| `article_id` | TEXT FK articles | |
| `action` | TEXT | `open`, `save`, `unsave`, `dismiss`, `undismiss`, `rate` |
| `value` | INTEGER | Rating 1–5 for `rate`; otherwise NULL |
| `note` | TEXT | Optional note (e.g. `oyt save … -m "weekend idea"`) |
| `created_at` | TEXT | When |

---

## AI and cost

### llm_calls
The **cost ledger**: one row per model call attempt, including retries and failures, because they
cost too (ADR 0003). Written only by `llm/gateway.py`. Migration 0003.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | When the call finished |
| `purpose` | TEXT | `summarize`, `summarize_map`, `summarize_reduce`, `judge`, `token_count`, `compare`, `ping` |
| `article_id` | TEXT | Which story it was for (NULL for pings) |
| `provider` | TEXT | `ollama` (more later) |
| `model` | TEXT | e.g. `llama3.2:3b` |
| `prompt_version` | TEXT | e.g. `summary-v1`, `judge-v1` |
| `attempt` | INTEGER | 1 = first try, 2+ = retry |
| `status` | TEXT | `ok` · `invalid` (output didn't match the JSON schema) · `error` |
| `error` | TEXT | Why it failed |
| `input_tokens` | INTEGER | **From the model's response** (Ollama `prompt_eval_count`), never estimated |
| `output_tokens` | INTEGER | From the response (`eval_count`) |
| `cache_read_tokens` | INTEGER | Prompt-cache hits (paid providers; 0 for Ollama) |
| `cache_write_tokens` | INTEGER | Prompt-cache writes (paid providers) |
| `cost_usd` | REAL | Computed and stored at call time from `config/pricing.yaml` ($0 for Ollama) |
| `pricing_version` | TEXT | Which price list was used |
| `latency_ms` | INTEGER | Wall-clock time the app waited |
| `tokens_per_sec` | REAL | Generation speed |
| `timings` | TEXT JSON | Ollama detail: `load_ms`, `prompt_ms`, `generation_ms` |
| `request_id` | TEXT | Provider request id, for debugging |

### token_counts
How many tokens a summary of each story **would** use. Estimated nightly for every story and
measured for a calibration sample. Written by `services/tokens.py`. Migrations 0007, 0008.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `article_id` | TEXT FK articles | |
| `content_hash` | TEXT | Which text was counted |
| `prompt_version` | TEXT | Which summary prompt (`summary-v1`) |
| `method` | TEXT | `estimate` (tokenizer or ratio, no model) · `measured` (the model's real count) |
| `model` | TEXT | Whose tokenizer the count is for |
| `chars_per_token` | REAL | Ratio used (estimate) or observed (measured) |
| `system_chars` | INTEGER | Characters in the instructions |
| `article_chars` | INTEGER | Characters in title + numbered passages |
| `system_tokens` | INTEGER | Tokens in the instructions |
| `article_tokens` | INTEGER | Tokens in the article part |
| `input_tokens` | INTEGER | Everything sent: system + article + chat template (~30) |
| `output_budget` | INTEGER | Expected summary length (400) |
| `llm_call_id` | INTEGER FK llm_calls | The calibration call (measured rows only) |
| `created_at` | TEXT | When |
| `tokenizer` | TEXT | What counted it: `hf:<repo>` (exact), `ratio:4.00` (estimate), `ollama` (real) |

Unique per (`article_id`, `content_hash`, `prompt_version`, `method`, `model`): re-estimating
replaces the row.

### summaries
**One row per summary attempt**, successful or not, with estimated vs real cost (ADR 0006).
The current summary of a story = newest row with `summarization_done = 'yes'` for its current
`content_hash`, the active `prompt_version` and the model. Written by `services/summaries.py`.
Migrations 0008, 0009.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | Referenced by judgments and ratings |
| `article_id` | TEXT FK articles | |
| `content_hash` | TEXT | Which text was summarized |
| `prompt_version` | TEXT | `summary-v1` |
| `provider` | TEXT | `ollama` |
| `model` | TEXT | e.g. `llama3.2:3b` |
| `tokenizer` | TEXT | What produced the estimate |
| `estimated_input_tokens` | INTEGER | Counted before the call (all map calls + reduce, for map-reduce) |
| `estimated_output_tokens` | INTEGER | Expected output |
| `estimated_cost_usd` | REAL | Estimate × prices |
| `real_input_tokens` | INTEGER | **Sum over every ledger row of this attempt** (map calls, retries, reduce) |
| `real_output_tokens` | INTEGER | Same, output |
| `real_cost_usd` | REAL | Same, cost |
| `summarization_done` | TEXT | `yes` · `no` |
| `description` | TEXT | Why it's `no`: no usable text, invalid JSON, no valid citations, model error |
| `summary_json` | TEXT JSON | The summary: `{tldr, key_points: [{text, citations, quote}], why_it_matters}` (only when `yes`) |
| `llm_call_id` | INTEGER FK llm_calls | The final call (single pass, or the reduce call) |
| `latency_ms` | INTEGER | Wall-clock time for the whole attempt |
| `created_at` | TEXT | When |
| `mode` | TEXT | `single` · `map_reduce` |
| `chunks` | INTEGER | Map-reduce: chunks that produced notes |
| `partial` | INTEGER 0/1 | 1 = longer than 12 chunks; only the first 12 were summarized |
| `calls` | INTEGER | Model calls in this attempt, including retries |
| `checks_json` | TEXT JSON | Deterministic checks: `quotes_exact`, `quotes_fuzzy`, `quote_elsewhere`, `coverage`, `invalid_citations_removed`, `points_dropped`, `duplicate_points`, `passed`, … |
| `trigger` | TEXT | `nightly` · `on_demand` (app or `oyt summarize`) · `eval` |

---

## Evals

### eval_golden
The **golden set**: stories picked for careful, human-labelled evaluation. Written by
`oyt eval golden` (`services/evals.py`). Migration 0009.

| Column | Type | Meaning |
|---|---|---|
| `article_id` | TEXT PK, FK articles | |
| `bucket` | TEXT | Why it was picked: `short` · `medium` · `long` (needs map-reduce) |
| `added_at` | TEXT | When |
| `note` | TEXT | Optional |

### claim_judgments
**One verdict per key point per judge.** `judge = 'human'` is you (ground truth); any other value
is the LLM judge's model name. Comparing the two gives Cohen's kappa. Written by `oyt eval label`
and `oyt eval judge`. Migration 0009.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `summary_id` | INTEGER FK summaries | |
| `point_idx` | INTEGER | 1-based position in the summary's `key_points` |
| `judge` | TEXT | `human` or a model name (e.g. `qwen3.5:4b`) |
| `verdict` | TEXT | `supported` · `partial` · `unsupported` |
| `reason` | TEXT | The judge's one-line reason, or your note |
| `prompt_version` | TEXT | Judge prompt (`judge-v1`); NULL for you |
| `llm_call_id` | INTEGER FK llm_calls | The judge's call |
| `created_at` | TEXT | When |

Unique per (`summary_id`, `point_idx`, `judge`).

### summary_ratings
Whole-summary usefulness, from 1 (useless) to 5 (would read it instead of the article). Written by
`oyt eval label`. Migration 0009.

| Column | Type | Meaning |
|---|---|---|
| `summary_id` | INTEGER PK, FK summaries | |
| `judge` | TEXT PK | `human` (room for LLM ratings later) |
| `usefulness` | INTEGER | 1–5 |
| `note` | TEXT | Optional |
| `created_at` | TEXT | When |

---

## Operations

### ingestion_runs
One row **per source per ingest**: did it work, and what did it bring. Written by
`ingestion/pipeline.py`. Migrations 0001, 0006.

| Column | Type | Meaning |
|---|---|---|
| `run_id` | TEXT PK | Groups all sources of one ingest |
| `source_id` | TEXT PK | Source id from `sources.yaml` |
| `started_at` | TEXT | |
| `finished_at` | TEXT | |
| `ok` | INTEGER 0/1 | Did the fetch succeed |
| `fetched` | INTEGER | Items the source returned |
| `new` | INTEGER | Items never seen before |
| `merged` | INTEGER | Items that turned out to be an existing story (deduped) |
| `updated` | INTEGER | Existing stories whose points or comments were refreshed |
| `skipped` | INTEGER | Dropped: too old, future-dated, no URL |
| `error` | TEXT | Why it failed |
| `counts` | INTEGER 0/1 | 0 = failed during a network outage: ignored for the "failed 2 runs in a row" alert |

### pipeline_runs
One row per **refresh**: the 1 AM job or a manual run. Written by `services/nightly.py`.
Migration 0005.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `run_id` | TEXT unique | Shown by `oyt nightly` (e.g. `0a3c845c22dd`) |
| `trigger` | TEXT | `scheduled` · `manual` (counted for a future paid "extra refresh") |
| `started_at` | TEXT | |
| `finished_at` | TEXT | |
| `status` | TEXT | `running` · `ok` · `partial` (finished with failures) · `failed` (crashed) · `skipped` (offline) |
| `steps` | TEXT JSON | Per-step counts: `ingest`, `extract`, `retag`, `tokens`, `summaries`, `network_outage` |
| `error` | TEXT | Crash or skip reason |

### alerts
Things a developer should check. Shown under **Needs attention** in the app, in `oyt alerts`, and as
a macOS notification. Deduplicated on (`kind`, `subject`) while open. Written by
`services/nightly.py`. Migration 0005.

| Column | Type | Meaning |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | |
| `kind` | TEXT | `source_failing` · `extraction_gave_up` · `pipeline_failed` · `network_down` · `llm_unavailable` · `summaries_unfinished` |
| `subject` | TEXT | What it's about: source id, website, `network`, `llm`, `summaries` |
| `message` | TEXT | Plain-English explanation shown in the app |
| `details` | TEXT JSON | Run id, counts, sample errors |
| `resolved_at` | TEXT | NULL = open. Some kinds resolve themselves when things work again |

---

## Handy queries

```sql
-- Today's summaries: done vs failed, and why
SELECT summarization_done, description, COUNT(*) FROM summaries
WHERE created_at >= date('now') GROUP BY 1, 2 ORDER BY 3 DESC;

-- Estimated vs real input tokens per summary (estimate accuracy)
SELECT article_id, mode, estimated_input_tokens, real_input_tokens,
       ROUND(100.0 * (estimated_input_tokens - real_input_tokens) / real_input_tokens, 1) AS err_pct
FROM summaries WHERE summarization_done = 'yes' ORDER BY id DESC LIMIT 20;

-- Read one summary's key points
SELECT json_extract(value, '$.text'), json_extract(value, '$.citations')
FROM summaries, json_each(json_extract(summary_json, '$.key_points')) WHERE summaries.id = 1;

-- Quote match rate per model (deterministic check)
SELECT model, AVG((json_extract(checks_json,'$.quotes_exact') + json_extract(checks_json,'$.quotes_fuzzy'))
                  * 1.0 / NULLIF(json_extract(checks_json,'$.quotes'), 0)) AS quote_match
FROM summaries WHERE summarization_done = 'yes' GROUP BY model;

-- You vs the judge, claim by claim
SELECT h.summary_id, h.point_idx, h.verdict AS you, j.verdict AS judge, j.reason
FROM claim_judgments h JOIN claim_judgments j
  ON j.summary_id = h.summary_id AND j.point_idx = h.point_idx AND j.judge != 'human'
WHERE h.judge = 'human';

-- What the model time went on, by purpose
SELECT purpose, COUNT(*), SUM(input_tokens), SUM(output_tokens), ROUND(SUM(latency_ms)/60000.0, 1) AS minutes
FROM llm_calls GROUP BY purpose ORDER BY minutes DESC;

-- Why pages became excerpts or failed
SELECT status, error, COUNT(*) FROM article_content GROUP BY status, error ORDER BY 3 DESC;
```
