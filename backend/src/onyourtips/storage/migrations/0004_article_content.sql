-- 0004: full text of selected articles, and the numbered passages citations point to (ADR 0002).
CREATE TABLE IF NOT EXISTS article_content (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id        TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    content_hash      TEXT,                    -- sha256 of the normalized text: the identity of this input
    extractor         TEXT NOT NULL,           -- trafilatura | html-fallback | github-readme | feed
    extractor_version TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    text              TEXT,
    word_count        INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL,           -- ok | excerpt (only the feed's description) | skipped | failed
    error             TEXT,
    fetched_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_article_content_article ON article_content(article_id, id);

CREATE TABLE IF NOT EXISTS passages (
    content_hash TEXT NOT NULL,
    idx          INTEGER NOT NULL,             -- 1..N in reading order; citations use this number
    text         TEXT NOT NULL,
    char_start   INTEGER NOT NULL,             -- offsets into article_content.text, for highlighting
    char_end     INTEGER NOT NULL,
    PRIMARY KEY (content_hash, idx)
);
