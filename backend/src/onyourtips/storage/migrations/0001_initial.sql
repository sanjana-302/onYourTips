-- 0001: articles, sightings, ingestion runs
-- Uses IF NOT EXISTS so databases created before migrations existed are adopted safely.

CREATE TABLE IF NOT EXISTS articles (
    id                TEXT PRIMARY KEY,
    canonical_url     TEXT NOT NULL UNIQUE,
    url               TEXT NOT NULL,
    title             TEXT NOT NULL,
    title_fp          TEXT,
    summary           TEXT,
    author            TEXT,
    content_type      TEXT NOT NULL,
    published_at      TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    topics            TEXT NOT NULL DEFAULT '[]',
    source_tags       TEXT NOT NULL DEFAULT '[]',
    primary_source_id TEXT NOT NULL,
    signal            REAL NOT NULL DEFAULT 0,
    extra             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS ix_articles_title_fp  ON articles(title_fp);

CREATE TABLE IF NOT EXISTS sightings (
    article_id     TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    source_id      TEXT NOT NULL,
    external_id    TEXT,
    discussion_url TEXT,
    points         INTEGER,
    comments       INTEGER,
    stars          INTEGER,
    seen_at        TEXT NOT NULL,
    PRIMARY KEY (article_id, source_id)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    fetched     INTEGER NOT NULL,
    new         INTEGER NOT NULL,
    merged      INTEGER NOT NULL,
    updated     INTEGER NOT NULL,
    skipped     INTEGER NOT NULL,
    error       TEXT,
    PRIMARY KEY (run_id, source_id)
);
