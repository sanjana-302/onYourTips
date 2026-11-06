-- 0007: how many tokens a summary of each article would use (cost calibration, ADR 0003).
--
-- method = estimate: characters / chars_per_token, computed nightly for every article. Free.
-- method = measured: the model's real prompt token count (Ollama prompt_eval_count), for a
--                    sample of articles, via `oyt tokens calibrate`. Recorded in the ledger too.
-- Measured rows set the chars-per-token ratio that later estimates use.
CREATE TABLE IF NOT EXISTS token_counts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL REFERENCES articles(id),
    content_hash    TEXT NOT NULL,          -- which extracted text was counted
    prompt_version  TEXT NOT NULL,          -- which summary prompt (e.g. summary-draft-1)
    method          TEXT NOT NULL,          -- estimate | measured
    model           TEXT NOT NULL,          -- whose tokenizer the count is for
    chars_per_token REAL,                   -- ratio used (estimate) or observed (measured)
    system_chars    INTEGER NOT NULL,       -- instructions
    article_chars   INTEGER NOT NULL,       -- title + numbered passages
    system_tokens   INTEGER NOT NULL,
    article_tokens  INTEGER NOT NULL,
    input_tokens    INTEGER NOT NULL,       -- everything sent: system + article + template
    output_budget   INTEGER NOT NULL,       -- expected summary length in tokens
    llm_call_id     INTEGER REFERENCES llm_calls(id),  -- measured rows only
    created_at      TEXT NOT NULL,
    UNIQUE (article_id, content_hash, prompt_version, method, model)
);
CREATE INDEX IF NOT EXISTS idx_token_counts_model ON token_counts(model, method, prompt_version);
