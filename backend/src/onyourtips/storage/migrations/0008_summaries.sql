-- 0008: exact tokenizers + one row per summary attempt (ADR 0002, ADR 0003).

-- Which counter produced a count: hf:<repo> (the model's own tokenizer), ratio:<chars/token>
-- (fallback estimate), or ollama (the model's real count, from calibration).
ALTER TABLE token_counts ADD COLUMN tokenizer TEXT;

-- One row per summary attempt, successful or not. Written before the call (estimate) and
-- completed after it (real numbers), so every attempt has an estimate to compare against.
CREATE TABLE IF NOT EXISTS summaries (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id               TEXT NOT NULL REFERENCES articles(id),
    content_hash             TEXT,              -- which extracted text was summarized
    prompt_version           TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    model                    TEXT NOT NULL,
    tokenizer                TEXT,              -- what produced the estimate
    estimated_input_tokens   INTEGER NOT NULL,
    estimated_output_tokens  INTEGER NOT NULL,  -- expected summary length
    estimated_cost_usd       REAL NOT NULL,
    real_input_tokens        INTEGER,           -- from the response's usage data
    real_output_tokens       INTEGER,
    real_cost_usd            REAL,              -- from the ledger (llm_calls.cost_usd)
    summarization_done       TEXT NOT NULL CHECK (summarization_done IN ('yes', 'no')),
    description              TEXT,              -- why it wasn't done (too long, invalid JSON, ...)
    summary_json             TEXT,              -- the validated summary, when done = yes
    llm_call_id              INTEGER REFERENCES llm_calls(id),  -- NULL if never sent
    latency_ms               INTEGER,
    created_at               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_article ON summaries(article_id, created_at);
