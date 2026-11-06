-- 0002: append-only log of user interactions (open/save/dismiss/rate)
-- Uses IF NOT EXISTS so databases created before migrations existed are adopted safely.

CREATE TABLE IF NOT EXISTS interactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    action     TEXT NOT NULL,
    value      INTEGER,
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_interactions_article ON interactions(article_id, id);
CREATE INDEX IF NOT EXISTS ix_interactions_created ON interactions(created_at);
