# Design log book

Every place where what we built differs from what we first designed, and why. ADRs record
decisions; this log records **changes of mind**, including small ones that never got their own ADR.

**How to add an entry:** append it to the right section and add a row to the index. Write what the
original design said, what we did instead, why, and what would make us revisit it. Don't edit old
entries; if a decision changes again, add a new entry that points back to the old one.

Baseline: `architecture-v0.1` and ADRs 0001–0003 (1 Nov 2026), then ADRs 0004–0005 (6 Nov 2026).
Status: **Foundation complete** (ingestion, digest, API, app, LLM gateway, cost ledger, model
compare, full-text extraction, daily pipeline). Next: summaries with citations.

## Index

| # | Area | Originally | Now | Ref |
|---|---|---|---|---|
| 1 | Schedule | `oyt ingest` 4× a day | One `oyt nightly` at 01:00 | ADR 0005 |
| 2 | Hacker News | Front-page snapshot | Best of the last 24 h (`window_hours` + `min_points`) | ADR 0005 |
| 3 | Extraction scope | Selected stories only | Every new story; AI work stays selective | ADR 0005 widens 0002 |
| 4 | Extraction statuses | `ok / paywalled / failed / too_short` | `ok / excerpt / skipped / failed` + `retryable` | migrations 0004–0005 |
| 5 | Failure handling | Not designed | Retry transient failures once, then give up + alert | ADR 0005 |
| 6 | Network outages | Not designed | Preflight check; outage failures don't count | ADR 0005, migration 0006 |
| 7 | Tagging | Tag once at ingest, title + feed blurb | Re-tag after extraction with the article's opening | ADR 0005 |
| 8 | Refresh button | Starts an ingest | Runs the full pipeline, recorded as `manual` | ADR 0005 |
| 9 | Alerts | Not designed | `alerts` table, app panel, `oyt alerts`, macOS notification | ADR 0005 |
| 10 | LLM provider | Paid API (Claude), money budgets | Local Ollama first; daily call limit is the main guard | ADR 0004 |
| 11 | Cost estimate before a call | "≈ $0.004" shown before generating | Token counts per article first; prices later | ADR 0003, migration 0007 |
| 23 | Token counting library | Not specified | Hugging Face `tokenizers`, not LiteLLM | migration 0008 |
| 24 | `summaries` table | ADR 0002: `status` ok / invalid / error, unique per input | Estimated vs real cost, `summarization_done` yes/no + `description`, one row per attempt | migration 0008 |
| 12 | What the ledger measures | Tokens and dollars | Plus speed, load time and latency, the real local costs | ADR 0004 |
| 13 | Context size | Not specified | `num_ctx = 8192`, set on every request | ADR 0004 |
| 14 | Summaries | Built right after the gateway | Deferred until after the foundation | Roadmap |
| 15 | Module imports | `storage` imports only `core`, `ranking` | `storage` imports `llm` types for type checking only | ADR 0001 |
| 16 | Module name | `llm/types.py` | `llm/models.py` | — |
| 17 | Repo layout | `infra/` (Docker) planned | Not created yet | ADR 0001 |
| 18 | Frontend | "Later" | React + Vite + TS web app, served by FastAPI, installable | — |
| 19 | Theme | Follows the phone (light + dark) | Light only | — |
| 20 | Phone access | Not specified | Plain `http://` on home Wi-Fi, no TLS, no login | README |
| 21 | Storage size | "Unlimited, keep full history" | Still kept; full text makes retention a real question | ADR 0005 |
| 22 | Content rights | Not considered | Stored full text is for the owner only | ADR 0005 |

---

## Ingestion and the daily pipeline

### 1. One refresh a day instead of four (6 Nov)
- **Originally:** launchd ran `oyt ingest` four times a day. Ingest only; nothing else ran on a schedule.
- **Now:** one `oyt nightly` at 01:00 local time (ingest → extract → retag → alerts → record).
- **Why:** the product's model is "fresh every morning; extra refreshes may become paid". With
  only one run, that run has to do everything, not just ingest.
- **Revisit when:** paid extra refreshes are built, or sources need to be fresher than a day.
  Changing the schedule is a config change, because the pipeline is the same.

### 2. Hacker News: best of the day, not a snapshot (6 Nov)
- **Originally:** fetch the front page at ingest time.
- **Now:** Algolia search over the last 24 hours (`window_hours: 24`) with a points threshold
  (`min_points: 50` for top stories, `10` for Show HN).
- **Why:** with one run a day, a front-page snapshot at 1 AM misses stories that rose and fell
  during the day. Several snapshots a day used to hide that.
- **Revisit when:** the refresh frequency changes again.

### 3. Full text for every new story, not just selected ones (6 Nov)
- **Originally (ADR 0002):** extract only digest picks, saved stories, and stories opened in the app.
- **Now:** every story first seen in the last 7 days is extracted every night, about 500 a day.
- **Why:** while exploring the database, you found `article_content` and `passages` empty,
  because nothing ever requested extraction. Extraction and passage splitting are deterministic
  (no LLM, no AI cost). At one user and ~500 stories a day it's cheap, and later features need
  text for every story (embeddings, search, quality filter, better tags).
- **Boundary kept:** this applies to **deterministic work only**. LLM work such as summaries stays
  selective, as ADR 0002 says.
- **Revisit when:** many more sources (a job queue with per-domain rate limits), or storage size.

### 4. Different extraction statuses (6 Nov)
- **Originally (ADR 0002):** `ok / paywalled / failed / too_short`.
- **Now:** `ok / excerpt / skipped / failed`, plus a `retryable` flag (migration 0005) and
  `counts_as_attempt` (migration 0006).
- **Why:** "paywalled" and "too short" lead to the same result in practice: use the feed's
  description instead. That's `excerpt`, which is still usable text. `skipped` separates "not an
  article" (PDF, video, robots.txt says no) from real failures. The reason stays in `error`.

### 5. Retry once, then give up and alert (6 Nov)
- **Originally:** no failure policy.
- **Now:** timeouts, connection errors, HTTP 5xx and 429 are marked `retryable` and tried again
  the next night. After 2 failed attempts the page is given up on and never fetched again, with
  one `extraction_gave_up` alert **per website**. Expected outcomes (403, 404, paywall, robots.txt)
  are final, so they're never retried and never alerted.
- **Why:** your rule: "pages that fail for 2 runs in a row: stop looking them up and alert the
  developer". Retrying forever wastes requests and hides problems. Grouping by website keeps it to
  one alert per cause.

### 6. A network outage isn't hundreds of failures (6 Nov)
- **Originally:** not designed. Every failure counted.
- **What happened:** the first real manual run happened while the Mac's DNS was down. All 20
  sources and 327 page fetches failed with `nodename nor servname provided`. Each page lost one of
  its two attempts, and sources moved toward "failing" alerts.
- **Now:** before fetching anything, the pipeline checks that it can look up a few hostnames.
  - If it can't, the run is recorded as `skipped`, raises one `network_down` alert, and counts nothing.
  - If at least half the sources or pages (and at least 5) fail with DNS or "unreachable" errors,
    those failures are discounted, and one alert replaces the per-site alerts.
  - Migration 0006 repaired that first run's rows.
- **Why:** a failure caused by your own network says nothing about the websites.
- **Lesson:** design failure handling around *who* is at fault (us, the network, the site), not
  just *whether* something failed.

### 7. Re-tag with the full text (6 Nov)
- **Originally:** topics were tagged once at ingest, from the title and the feed's one-line blurb.
- **Now:** after extraction, the same keyword tagger runs again on title + blurb + the **first
  1,500 characters** of the full text. In the first real run, 72 of 249 stories changed topics.
- **Why:** blurbs are often empty or vague. Only the opening is used, because the whole text
  mentions many topics in passing and would over-tag. Still string matching, so no AI cost.
- **Revisit at:** roadmap feature #6 (smarter tagging with embeddings).

### 8. The Refresh button runs the whole pipeline (6 Nov)
- **Originally:** `POST /api/ingest` ran an ingest only.
- **Now:** it runs the same pipeline as 1 AM, recorded in `pipeline_runs` as `trigger = manual`.
  The app shows "Updated 6 hr. ago, next at 1:00 AM".
- **Why:** one code path means a manual refresh tests exactly what runs at night. Recording
  manual runs is the hook for paid extra refreshes later.

### 9. Alerts (6 Nov)
- **Originally:** failures were only in logs and `ingestion_runs`.
- **Now:** an `alerts` table, shown under **Needs attention** in the You tab, in `oyt alerts`, and
  as a macOS notification. Alert kinds:
  - `source_failing` (2 runs in a row)
  - `extraction_gave_up`
  - `pipeline_failed`
  - `network_down`

  `source_failing` and `network_down` resolve themselves when things work again.
- **Why:** the 1 AM run happens while you sleep, so nobody reads its log. Problems have to come
  to you.

## AI foundation

### 10. Local models first, not a paid API (6 Nov)
- **Originally (ADR 0002–0003):** a paid provider. It used Claude's built-in citations; money
  budgets (daily and monthly USD) were the guard, and cost was shown in dollars.
- **Now (ADR 0004):** Ollama, running `llama3.2:3b` locally, behind the same gateway and ledger.
  Prices are $0 in `pricing.yaml`. A daily **call limit** (2,000) is the main guard; the money
  budgets stay in place for later.
- **Why:** this is a learning project. Free experiments, and you can see how models behave and
  improve (`oyt llm compare`).
- **Important correction:** Ollama is **not free**; it costs laptop time, memory, battery and
  heat. It's for testing. The provider choice for real summaries is still open, to be decided
  when we design summaries with citations.
- **Revisit when:** building summaries (feature #3). A paid provider is one more class in
  `llm/providers.py` plus entries in `pricing.yaml`.

### 11. Token counts first, prices later (6 Nov)
- **Originally (ADR 0003):** count input tokens and show "≈ $0.004" before generating.
- **First change:** not built, since every estimate would be $0 with Ollama.
- **Now:** measure **how many tokens a summary of each article would use**, before summaries
  exist, and apply prices later.
  - **Draft summary prompt** (`summary-draft-1`): instructions plus numbered passages. The counts
    cover what a real call will send, not just the article text.
  - **Free estimate for every article, every night:** characters ÷ characters-per-token. Stored
    in `token_counts` with `method = estimate`.
  - **Calibration:** `oyt tokens calibrate` sends a sample of articles to Ollama and records the
    real prompt token counts, both in `token_counts` (`method = measured`) and in the ledger
    (purpose `token_count`). These set the characters-per-token ratio that estimates use.
  - **`oyt tokens`** shows the typical, high-end and largest article, how many wouldn't fit the
    context window, daily totals, and projected cost for any paid model in `pricing.yaml`.
- **Why:** your suggestion. Real token counts are the base that any cost estimate needs. They
  also show which articles are too long *before* a summary silently cuts them off.
- **Gotchas found while building it:**
  - Each model's tokenizer splits text differently, so the ratio is stored per model.
  - Ollama doesn't re-count a prompt start it has cached, so calibration measures each message
    on its own and discards counts that are far too low.
  - A prompt longer than the context window is cut off silently, so calibration sizes the window
    per article and measures only 2k–24k-character articles.
- **First numbers (rule of thumb, 487 articles with text):**

  | Measure | Tokens |
  |---|---|
  | Median | ~470 |
  | 90th percentile | ~3,000 |
  | Total, all articles | ~770k |

  6 articles wouldn't fit 8,192 tokens. The largest is a 138k-token OpenSSH page.
- **Revisit when:** the real summary prompt is written (new `prompt_version`), or a paid
  provider is priced.

### 12. The ledger measures time, not just money (6 Nov)
- **Originally:** tokens, cost in USD, latency.
- **Now:** also tokens per second, model load time, and prompt and generation time (from
  Ollama's response).
- **Why:** on a laptop, time is the real cost. Cold model loads dominate the first call.

### 13. Context size set on every request (6 Nov)
- **Originally:** not specified.
- **Now:** `num_ctx = 8192` is sent with every request.
- **Why:** Ollama defaults to 4,096 tokens and **silently cuts off** longer input, which would make
  summaries of long articles quietly wrong.

### 14. Summaries come after the foundation
- **Originally:** summaries with citations were the next feature after the gateway.
- **Now:** deferred. First the gateway, ledger, model comparison, extraction, the daily pipeline
  and its failure handling. To be discussed "in later stages".
- **Why:** summaries need reliable full text and passages underneath them, so citations point
  at real passages. That foundation now exists, with ~500 extracted stories a day.

## Code structure

### 15. `storage` imports `llm` types, for type checking only
- **Originally (ADR 0001):** `storage` may import `core` and `ranking.scoring`. `llm` may import
  `storage`.
- **Now:** `storage/db.py` imports `LLMCallRecord` from `llm` under `if TYPE_CHECKING:`, so it
  only happens during type checking, never at runtime.
- **Why:** the ledger insert needs the record's type, but a runtime import would be circular
  (llm → storage → llm).
- **Better later:** move `LLMCallRecord` into `core/models.py`, which restores the rule.

### 16. `llm/types.py` renamed to `llm/models.py`
- **Why:** a module named `types` shadows Python's built-in `types` module and broke imports.
- **Lesson:** never name a module after a standard-library module.

### 17. No `infra/` folder yet
- **Originally (ADR 0001):** `infra/` for Docker, later.
- **Now:** not created. Everything runs directly on the Mac with uv, and launchd handles the schedule.
- **Revisit when:** moving to a server, Postgres, or more than one user.

## App and UI

### 18. A real mobile app now, not "later"
- **Originally:** "frontend (planned)".
- **Now:** a React 19 + Vite + TypeScript web app, built into `frontend/dist` and served by the
  same FastAPI process (`oyt serve`). It can be added to the phone's Home Screen (PWA manifest)
  and uses hash routing. The tabs are Today, Week, Saved and You.
- **Why:** you wanted to use it daily on your phone and test it locally. Serving it from FastAPI
  means one process and one address, with no separate web server.

### 19. Light mode only (6 Nov)
- **Originally:** the styles followed the phone's setting (light and dark themes).
- **Now:** always light; the dark theme was removed.
- **Why:** your palette and reference UI are light designs. With your phone in dark mode, the
  app looked wrong.
- **Revisit when:** someone asks for dark mode. It would need its own palette, not inverted colors.

### 20. Plain http on home Wi-Fi, no login
- **Now:** the phone opens `http://<mac-ip>:8000`. Typing `https://` produced uvicorn's "Invalid
  HTTP request received" warnings, because the server doesn't speak TLS.
- **Why:** one user on a trusted home network. TLS and login are for when it's hosted.
- **Revisit when:** it's used outside home Wi-Fi, or by anyone else.

## Data

### 21. Keep everything, but now it adds up
- **Originally:** "storage unlimited; full history is kept (needed for recommendations)".
- **Now:** still keeping everything, including full text: about 5–15 KB per story, roughly
  2–3 GB a year.
- **Revisit:** add a retention rule, for example dropping full text after 90 days unless saved.
  Keep article metadata and interactions forever.

### 22. Content rights
- **Originally:** not considered.
- **Now:** stored full text is for the owner's personal reading and for computing passages,
  tags and embeddings. With other users, the app shows summaries and a link to the original,
  never the stored full text. Also noted in the AI roadmap's scaling sheet.

## Cost tracking

### 23. Hugging Face `tokenizers` for exact counts, not LiteLLM (6 Nov)
- **Asked for:** LiteLLM's `token_counter`, instead of our own characters ÷ ratio estimate.
- **Now:** the `tokenizers` library loads the model's own tokenizer file (for `llama3.2`, a
  public copy of Meta's `tokenizer.json`, listed in `config/tokenizers.yaml`). It's downloaded
  once and cached. Counts match Ollama's for the text itself; the chat template adds about 30
  tokens. Models without a listed tokenizer fall back to the calibrated ratio. Every count
  records which counter produced it (`hf:<repo>`, `ratio:<n>`, or `ollama` for real counts).
- **Why not LiteLLM:**
  - **Supply chain:** its PyPI package was compromised twice in 2026 (1.82.7/1.82.8 in March,
    1.107.0 on 21 October). The malware ran at every Python start and stole SSH keys, `.env`
    files and cloud credentials, on the same laptop that holds yours.
  - **Accuracy:** for Ollama models it counts with OpenAI's tokenizer (tiktoken) unless you
    configure a Hugging Face one, so for llama it would still be an estimate.
  - **Where LiteLLM would help:** its price list for paid models. Revisit when a paid provider
    is added, pinned to a checked version with hashes.
- **Calibration stays,** now as the accuracy check: `oyt tokens` shows how far the counts are
  from Ollama's real ones.

### 24. The `summaries` table records estimated vs real cost per attempt (6 Nov)
- **Originally (ADR 0002):** `summaries` held `status` (ok / invalid / error) and a link to the
  ledger, with one row per article + text + prompt + model (a cache key).
- **Now (migration 0008):**
  - **One row per attempt,** including failures and stories that were never sent.
  - **Estimate:** `estimated_input_tokens`, `estimated_output_tokens`, `estimated_cost_usd`,
    and the `tokenizer` that produced them.
  - **Real numbers:** `real_input_tokens`, `real_output_tokens`, `real_cost_usd`, from the
    response's usage data and the ledger.
  - **Outcome:** `summarization_done` (`yes` / `no`) and `description`, the reason when it's
    `no` (for example "too long: 12,345 input + 400 output tokens > 8,192-token context window",
    invalid JSON, or a provider error).
- **Why:** your request. Comparing estimated and real cost per attempt is what calibrates
  pricing. Recording "too long" *before* sending is the fix for prompts that would be cut off
  silently. A cached summary is now "the latest row with `summarization_done = yes`" for the
  same text, prompt and model.
- **Status:** the table and the code that fills it (`services/summaries.py`: `plan_summary`,
  `record_summary`) are built. The summarize feature will call them.

---

## Open questions carried forward

- **Summaries:** which provider for real use (local or paid), and which stories get summarized.
- **Broken sources:**
  - Lobsters' feed now returns 404 and needs a new address.
  - Reddit rate-limits r/LocalLLaMA (429), so Reddit requests need spacing out.
- **Retention:** the rule for full text (entry 21).
- **Import rule:** move `LLMCallRecord` to `core` (entry 15).
