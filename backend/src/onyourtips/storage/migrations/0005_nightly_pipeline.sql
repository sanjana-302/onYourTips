-- 0005: daily refresh pipeline (ADR 0005).

-- An extraction attempt that failed for a reason worth retrying (timeout, 5xx, rate limit).
-- Articles are retried on the next nightly run; after 2 failed attempts they are given up on.
ALTER TABLE article_content ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0;

-- One row per run of the refresh pipeline, scheduled (1 AM) or manual (Refresh button).
-- Manual runs are recorded so extra refreshes can become a paid feature later.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL UNIQUE,
    trigger     TEXT NOT NULL,          -- scheduled | manual
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | ok | partial | failed
    steps       TEXT NOT NULL DEFAULT '{}',   -- per-step counts, e.g. {"ingest": {...}, "extract": {...}}
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_pipeline_runs_started ON pipeline_runs(started_at);

-- Things a developer should look at (a source failing two nights running, pages given up on).
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- source_failing | extraction_gave_up | pipeline_failed
    subject     TEXT NOT NULL,          -- source id, domain, ...
    message     TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{}',
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_open ON alerts(resolved_at, kind, subject);
