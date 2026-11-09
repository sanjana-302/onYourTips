# Architecture

## System overview

```
                       ┌──────────── backend/src/onyourtips ─────────────┐
 scheduled worker ──►  │  cli ─┐                                         │
 (launchd: oyt ingest) │       ├──► services ──► ingestion ──► sources   │──► HN, GitHub, arXiv, HF,
 frontend (planned) ─► │  api ─┘        │            │                   │    Reddit, dev.to, RSS
                       │                ├──► ranking (pure)              │
                       │                ├──► enrichment ─► llm (planned) │──► LLM provider
                       │                └──► storage ──► SQLite          │
                       └─────────────────────────────────────────────────┘
```

Module responsibilities and allowed imports: [ADR 0001](adr/0001-repo-structure-and-storage.md).

## HTTP API and web app

`oyt serve` runs FastAPI (uvicorn) on `0.0.0.0:8000`: JSON under `/api`, the built web app
(`frontend/dist`) at `/`, interactive docs at `/docs`. Routers in `api/routes.py` are thin:
parse input → one `services` call → response model. Each request gets its own SQLite connection.
`POST /api/ingest` starts a background ingest on its own thread (one at a time). `GET /api/ingest`
reports progress, which the app polls.

| Endpoint | Purpose |
|---|---|
| `GET /api/feed?hours&topic&type&q&limit` | recency-ranked feed |
| `GET /api/digest?days` | best of the window (no decay, quotas, topic caps) |
| `GET /api/articles/{id}` | article + sightings + your state (id prefix ok) |
| `POST /api/articles/{id}/interactions` | `{action: open\|save\|unsave\|dismiss\|undismiss\|rate, value?}` |
| `GET /api/saved`, `/api/interests`, `/api/topics`, `/api/sources`, `/api/status` | lists and stats |
| `POST /api/ingest`, `GET /api/ingest` | start / poll a background refresh |

## Daily refresh pipeline (ADR 0005)

```
01:00 launchd ─► oyt nightly          (or the app's Refresh button → same pipeline, trigger=manual)
  0. preflight can this Mac resolve hostnames? no → status skipped + one network_down alert
  1. ingest    best of the last 24h per source (HN: window_hours=24 + min_points)
  2. extract   every story first seen in the last 7 days without final text
               transient failure (timeout, 5xx, 429) → retryable → next night → 2nd failure: give up
  3. retag     keyword tagger on title + summary + first 1,500 chars of full text
     tokens    estimate a summary's input tokens per article (chars ÷ chars-per-token, no model)
  4. alerts    source failing 2 runs in a row · pages given up on (per website) · pipeline crashed
               → alerts table, You tab "Needs attention", macOS notification; source alerts auto-resolve
  4b. summarize every new story with citations, best first, until 08:30 (ADR 0006)
  5. record    pipeline_runs row: trigger, status (ok | partial | failed | skipped), per-step counts
```

If the network drops mid-run (≥ half of sources or page fetches fail on DNS / no route), those
failures don't count as attempts or toward streaks, and one `network_down` alert is raised
(migration 0006: `counts_as_attempt`, `ingestion_runs.counts`).

No LLM calls, so no AI cost. Extraction cost grows with articles, not users.
API: `GET /api/status` (last/next refresh, open alerts), `GET /api/alerts`,
`POST /api/alerts/{id}/resolve`.

## AI foundation

```
feature code ──► services ──► LLMGateway.complete(request, schema?)
                                  │ 1. budget + daily call limit (ledger sums)
                                  │ 2. provider.chat()  ── OllamaProvider ─► http://localhost:11434/api/chat
                                  │    retry transient errors with backoff
                                  │ 3. validate JSON against a pydantic schema (structured output)
                                  └ 4. one llm_calls row per attempt: tokens, cost, latency,
                                       tokens/sec, load/prompt/generation ms, status
```

- Only `llm/providers.py` talks to model servers. Prices live in `config/pricing.yaml` (Ollama is $0).
- `oyt llm compare` runs the same task on several models (temperature 0, no retries) and saves
  a report in `backend/data/compare/`. See ADR 0004.
- API: `GET /api/llm/status`, `GET /api/costs?days&by=model|purpose|day`.
- **Token counts for summaries** (`services/tokens.py`, table `token_counts`): the draft summary
  prompt (`enrichment/summary_prompt.py`) plus each article's numbered passages. Estimated nightly
  for every article. `oyt tokens calibrate` measures a sample with the model (ledger purpose
  `token_count`) to set the chars-per-token ratio. `oyt tokens` / `GET /api/tokens?days` report
  median, 90th percentile, largest, articles over `num_ctx`, and projected cost per priced model.
  Counts come from the model's own Hugging Face tokenizer (`config/tokenizers.yaml`, `tokenizers`
  library), else characters ÷ calibrated chars-per-token.
- **Summary attempts** (`services/summaries.py`, table `summaries`, migration 0008):
  `plan_summary` builds the prompt, counts tokens, estimates cost and checks it fits the context
  window, with no model call. `record_summary` writes one row per attempt: estimated vs real
  tokens and cost, `summarization_done` yes/no, and `description` with the reason when it's no.

## Summaries with citations and evals (ADR 0006)

```
plan_summary ─► fits 8,192? ── yes ─► one call (summary-v1) ─────────────┐
     │ tokens, cost estimate    no ─► chunks ─► map × N ─► reduce ───────┤
     ▼                                                                   ▼
check_summary: repair invalid citations · gate (≥1 cited point) · metrics (quotes, coverage)
     ▼
summaries row: done yes/no + reason · estimated vs real tokens/cost (sum of ledger rows)
```

- `services/summaries.py`: `plan_summary`, `summarize_article`, `get_summary_view`,
  `summary_report`
- `services/evals.py`: golden set, labels, LLM judge, agreement (Cohen's kappa), comparison report
- Tables: `summaries`, `eval_golden`, `claim_judgments`, `summary_ratings` (migration 0009)
- API: `GET /api/articles/{id}/summary`, `POST /api/articles/{id}/summarize`. In the app,
  citation chips open the passage with the quote highlighted.
- Guide to the terms: [evals field guide](evals-field-guide.md).

## Full-text extraction

Every new story, in the nightly pipeline (deterministic, no AI cost); also on demand with `oyt extract`:

| URL | Strategy | Extractor |
|---|---|---|
| github.com/owner/repo | README via the GitHub API (+ repo description) | `github-readme` |
| arxiv.org/abs/... | abstract from the feed (PDFs later) | `feed` |
| PDF, YouTube, X, … | skipped; feed description used if present | `feed` |
| anything else | robots.txt check → fetch (one request per site at a time) → trafilatura (or BeautifulSoup fallback) | `trafilatura` / `html-fallback` |

Text shorter than 120 words is replaced by the feed description (`status = excerpt`). The text
is normalized, hashed (`content_hash`) and split into numbered passages: short headings merge
into the next paragraph and long paragraphs split at sentence boundaries (≤1200 chars), with
character offsets for highlighting. Tables: `article_content`, `passages` (migration 0004).
API: `GET /api/articles/{id}/content`, `POST /api/articles/{id}/extract`.

## Ingestion pipeline

```
config/sources.yaml ─► connectors ─► RawItem ──┐  (concurrent fetch, failures isolated per source)
                                               ▼
   1. canonicalize URL (https, strip utm/fbclid, drop www, unify arXiv versions)
   2. HTML → text, truncate, dates → UTC, drop stale/future dates
   3. infer content type from URL (github.com/owner/repo → project, arxiv → paper)
   4. rule-based topic tagging (config/taxonomy.yaml)
   5. dedupe: same canonical URL, or same title fingerprint within ±3 days
   6. merge sightings across sources → recompute engagement signal
                                               ▼
                    articles · sightings · ingestion_runs
```

Every step after fetch is deterministic: the same RawItems always produce the same rows.

## Data model

| Model | What it is |
|---|---|
| `SourceConfig` | One entry in `sources.yaml` (kind, content type, weight, limit, params) |
| `RawItem` | What a connector returns, before any cleaning |
| `Article` | Normalized entity, **unique per canonical URL** (`id = sha256(url)[:16]`) |
| `Sighting` | "Source X saw article Y with N points / comments / stars" |
| `Interaction` | Append-only log entry: open / save / unsave / dismiss / undismiss / rate |
| `ArticleState` | Current state derived by folding interactions |
| `FeedItem` | Article + sightings + score + state — the API response shape |

Built: `llm_calls` (ADR 0003), `article_content` and `passages` (ADR 0002). Next: `summaries`.

## Ranking

```
engagement = log1p(points + 1.5·comments + 0.5·stars)
signal     = Σ over sightings  weight(source) · (1 + engagement)      (stored)
feed score = signal · 0.5^(age_hours / 24)                           (computed at query time)
digest     = signal, no decay                                         (best of the week)
```

RSS feeds have no engagement data, so official lab blogs get a higher `weight`.

## Weekly digest

1. Candidates: last N days, `signal ≥ min_signal` (3), not dismissed, ranked by signal.
2. Primary topic = the article's rarest topic in the pool (`llms + ai-coding` → `ai-coding`).
3. Content-type quotas first (≥6 projects, ≥4 papers), then fill by score with ≤5 per primary
   topic. Caps are relaxed if slots stay empty.

## How much you get

| Stage | Limit |
|---|---|
| Fetch | `limit` per source (default 30, max 200): about 600 items per run at most |
| Freshness | items older than `OYT_MAX_AGE_DAYS` (7) are skipped at ingest |
| Storage | unlimited; full history is kept (needed for recommendations) |
| `oyt feed` | 20 items, 24h window, 24h half-life |
| `oyt digest` | 30 items, 7-day window, no decay, quality threshold, topic caps, type quotas |

## Interactions → interest profile

`oyt interests` folds the window's interactions into topic weights
(save +3, open +1, rating −2…+2, dismiss −2). This, plus saved articles, is the input for the
planned weekend-project recommender.

## Storage and migrations

SQLite at `backend/data/onyourtips.db`. Schema changes are numbered SQL files in
`storage/migrations/`, applied automatically when the database is opened (`PRAGMA user_version`).
