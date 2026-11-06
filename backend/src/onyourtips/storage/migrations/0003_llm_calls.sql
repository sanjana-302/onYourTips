-- 0003: cost ledger. One row per LLM call attempt, including failures and retries (ADR 0003).
CREATE TABLE IF NOT EXISTS llm_calls (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT NOT NULL,
    purpose            TEXT NOT NULL,          -- e.g. summarize, compare, ping
    article_id         TEXT,                   -- what the call was for, if anything
    provider           TEXT NOT NULL,          -- ollama, ...
    model              TEXT NOT NULL,
    prompt_version     TEXT,
    attempt            INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL,          -- ok | invalid (output failed validation) | error
    error              TEXT,
    input_tokens       INTEGER NOT NULL DEFAULT 0,  -- from the provider's usage data, never estimated
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0,      -- computed and stored at call time
    pricing_version    TEXT,
    latency_ms         INTEGER NOT NULL DEFAULT 0,   -- wall clock, as experienced by the app
    tokens_per_sec     REAL,                         -- generation speed (output tokens / generation time)
    timings            TEXT NOT NULL DEFAULT '{}',   -- provider detail, e.g. Ollama load/prompt/generation ms
    request_id         TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_created ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS ix_llm_calls_article ON llm_calls(article_id);
