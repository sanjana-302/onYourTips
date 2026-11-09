-- 0009: summaries with citations (ADR 0006) and evals.

-- How a summary was made, and what the deterministic checks found.
ALTER TABLE summaries ADD COLUMN mode TEXT;            -- single | map_reduce
ALTER TABLE summaries ADD COLUMN chunks INTEGER;       -- map-reduce: parts summarized
ALTER TABLE summaries ADD COLUMN partial INTEGER NOT NULL DEFAULT 0;  -- 1 = only the first parts
ALTER TABLE summaries ADD COLUMN calls INTEGER;        -- model calls, incl. retries
ALTER TABLE summaries ADD COLUMN checks_json TEXT;     -- SummaryChecks (quotes, coverage, repairs)
ALTER TABLE summaries ADD COLUMN trigger TEXT;         -- nightly | on_demand | compare

CREATE INDEX IF NOT EXISTS idx_summaries_lookup
    ON summaries(content_hash, prompt_version, model, summarization_done);

-- Golden set: stories picked for careful, human-labelled evaluation.
CREATE TABLE IF NOT EXISTS eval_golden (
    article_id  TEXT PRIMARY KEY REFERENCES articles(id),
    bucket      TEXT NOT NULL,      -- why it was picked: short | medium | long (map-reduce)
    added_at    TEXT NOT NULL,
    note        TEXT
);

-- One verdict per key point per judge. judge = 'human' or the judging model's name.
-- Comparing the two is how we learn whether the LLM judge can be trusted.
CREATE TABLE IF NOT EXISTS claim_judgments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id   INTEGER NOT NULL REFERENCES summaries(id),
    point_idx    INTEGER NOT NULL,  -- 1-based position in key_points
    judge        TEXT NOT NULL,
    verdict      TEXT NOT NULL CHECK (verdict IN ('supported', 'partial', 'unsupported')),
    reason       TEXT,
    prompt_version TEXT,            -- judge prompt (LLM judges)
    llm_call_id  INTEGER REFERENCES llm_calls(id),
    created_at   TEXT NOT NULL,
    UNIQUE (summary_id, point_idx, judge)
);

-- Whole-summary usefulness, 1 (useless) to 5 (would read instead of the article).
CREATE TABLE IF NOT EXISTS summary_ratings (
    summary_id  INTEGER NOT NULL REFERENCES summaries(id),
    judge       TEXT NOT NULL,
    usefulness  INTEGER NOT NULL CHECK (usefulness BETWEEN 1 AND 5),
    note        TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (summary_id, judge)
);
