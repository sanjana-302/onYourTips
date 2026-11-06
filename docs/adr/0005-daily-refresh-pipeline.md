# ADR 0005: One daily refresh at 1 AM that extracts full text for every new story

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

Until now, ingestion ran four times a day and full-text extraction only ran when asked
(`oyt extract`), so `article_content` and `passages` stayed empty. The product direction is:

- **One refresh a day**, early enough that the morning feed is fresh. Extra refreshes may become
  a paid feature later.
- Extraction and passage splitting are deterministic (no LLM), so there's no AI cost. With one user
  and ~500 new stories a day, extracting **every** new story is cheap, and it gives later features
  (embeddings, search, quality filter, better tags) real text to work with.

## Decision

1. **`oyt nightly` runs at 01:00 local time** (launchd, `scripts/install_schedule.sh`). The Mac
   serves at that hour. The steps are:
   1. **ingest:** each source asks for the *best of the last 24 hours*. Hacker News uses
      `window_hours: 24` + `min_points` (instead of a front-page snapshot, which would miss stories
      that rose and fell during the day). Reddit and dev.to already use top-of-day, GitHub uses
      repos created in the last 7 days, and RSS feeds keep their recent items.
   2. **extract:** full text and numbered passages for every story first seen in the last 7 days
      that hasn't been extracted yet.
   3. **retry and give up:** an attempt that fails for a *transient* reason (timeout, connection
      error, HTTP 5xx or 429) is marked `retryable` and tried again the next night. After **2**
      failed attempts the page is given up on and never fetched again. Expected outcomes (paywall,
      403/404, robots.txt disallow, PDF/video, too short) are final and don't retry or alert.
   4. **retag:** the keyword tagger runs again on title + summary + the first 1,500 characters of
      the full text (see "How tagging works" below).
   5. **alerts** (`alerts` table, the app's **Needs attention** panel, `oyt alerts`, and a macOS
      notification):
      - `source_failing`: a source failed 2 runs in a row. Resolves itself when it works again.
      - `extraction_gave_up`: pages given up on, **grouped by website** (many failures on one site
        usually share a cause).
      - `pipeline_failed`: a step crashed. The run is still recorded.
   6. **record:** one `pipeline_runs` row per run with per-step counts and `trigger`
      (`scheduled` or `manual`).
2. **The Refresh button runs the same pipeline, recorded as `manual`.** It stays available for
   development. Counting manual runs is the hook for a paid "extra refresh" later. The app shows
   "Updated 6 hr. ago, next at 1:00 AM".
3. **Storage: keep everything for now.** Full text is about 5–15 KB per story, ~2–3 GB a year at
   500 stories a day. Revisit with a retention rule (for example, drop full text after 90 days
   unless saved).
4. **Content rights.** Stored full text is for the owner's personal reading and for computing
   (passages, tags, embeddings). With other users, the app shows summaries and a link to the
   original, never the stored full text.

## Network outages (added after the first real run)

The first manual run happened while the Mac's DNS was down: all 20 sources and 327 page fetches
failed with `nodename nor servname provided`. Treating that as hundreds of separate failures
used up one of every page's two attempts and pushed sources toward `source_failing` alerts.
Failures caused by the local network say nothing about the sites, so:

- **Preflight:** before fetching anything, the run resolves a few hostnames (no HTTP). If none
  resolves, the run is recorded as `skipped` with one `network_down` alert, and nothing counts.
- **Mid-run:** if at least half the sources or page fetches (and at least 5) fail with DNS or
  "network unreachable" errors, those failures are discounted
  (`article_content.counts_as_attempt = 0`, `ingestion_runs.counts = 0`): they don't use up an
  attempt or extend a failure streak, and one `network_down` alert replaces the per-site ones.
- `network_down` resolves itself on the next run that has a working network.
- Migration 0006 applied the same rule once to the rows from that first run.

## How tagging works (and why it costs nothing)

Tagging is rule-based (`ingestion/tagging.py`, `config/taxonomy.yaml`): each topic has a list of
keywords or regexes, matched case-insensitively on word boundaries. A story scores **+2** for each
keyword in its title or source tags and **+1** for each different keyword in the body text, and
gets the topic at **2 points**. One title hit is enough, but the body needs two *different*
keywords, so a passing mention doesn't tag a story. It's string matching on the CPU, so no model
and no AI cost. Re-tagging reads only the opening of the full text because the opening says what a
story is about; the whole text mentions many things in passing and would over-tag. The AI
upgrade is roadmap feature #6 ("smarter topic tagging" with embeddings).

## Scaling

Extraction cost grows with **articles, not users**: every user shares the same corpus. At 1,000 or
1,000,000 users it's still ~500 fetches a night for these sources. More sources mean a job queue
with workers and per-domain rate limits (see the roadmap's scaling columns).

## Alternatives considered

- **Refresh several times a day:** fresher, but against the product's "once a day, extras are paid"
  model. The pipeline is the same, so changing the schedule is a config change.
- **Extract only digest picks and saved stories (ADR 0002's original scope):** less fetching, but
  later features need text for every story, and at this volume the saving is negligible. This ADR
  widens ADR 0002's "selected only" to "all new stories", for deterministic work only. AI work
  stays selective.
- **Retry forever:** wastes requests on dead pages and hides problems. Two attempts and an alert
  makes problems visible.

## Consequences

- Every morning: fresh stories, with full text and passages for nearly all of them.
- Alerts make failures visible without reading logs.
- The Mac must be on and awake at 1 AM, with the API not required (`oyt nightly` runs on its own).
