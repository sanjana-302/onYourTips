"""Runtime settings, loaded from environment variables (prefix ``OYT_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ — so `oyt` works from any directory. (src/onyourtips/core/settings.py -> parents[3])
BACKEND_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OYT_", env_file=BACKEND_DIR / ".env", extra="ignore"
    )

    db_path: Path = BACKEND_DIR / "data" / "onyourtips.db"
    sources_file: Path = BACKEND_DIR / "config" / "sources.yaml"
    taxonomy_file: Path = BACKEND_DIR / "config" / "taxonomy.yaml"

    # Built web app served at / by the API (see frontend/).
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"
    # `oyt serve`: 0.0.0.0 makes it reachable from your phone on the same Wi-Fi.
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    user_agent: str = "OnYourTips/0.1 (personal news aggregator)"
    http_timeout: float = 20.0
    http_retries: int = 2
    http_backoff: float = 1.0
    concurrency: int = 6

    # Items published longer ago than this are ignored at ingestion time.
    max_age_days: int = 7
    summary_max_chars: int = 600
    # Ranking: score halves every N hours.
    half_life_hours: float = 24.0
    # Two different URLs with the same normalized title within this window are merged.
    title_dedupe_window_days: int = 3

    # Weekly digest (see ranking/digest.py). All overridable from the CLI.
    digest_days: int = 7
    digest_limit: int = 30
    digest_min_signal: float = 3.0
    digest_max_per_topic: int = 5
    digest_min_per_type: dict[str, int] = {"project": 6, "research_paper": 4}

    # ---- AI (see llm/ and docs/adr/0003, 0004)
    llm_provider: str = "ollama"
    # Default model for every AI task. Compare others with `oyt llm compare`.
    llm_model: str = "llama3.2:3b"
    ollama_url: str = "http://localhost:11434"
    llm_timeout: float = 180.0  # local models on a laptop can be slow, especially on first load
    llm_retries: int = 2
    llm_backoff: float = 1.0
    # Ollama's default context is 4096 tokens; articles need more.
    llm_num_ctx: int = 8192
    # Thinking models (e.g. qwen3.5): None = model default, False = answer directly.
    llm_think: bool | None = None
    pricing_file: Path = BACKEND_DIR / "config" / "pricing.yaml"
    # Guards. Local models cost $0, so the call limit is what stops a runaway loop.
    llm_daily_budget_usd: float = 1.0
    llm_monthly_budget_usd: float = 10.0
    llm_daily_call_limit: int = 2000
    # Token counting for summaries (services/tokens.py): expected summary length, and how many
    # articles `oyt tokens calibrate` measures by default.
    summary_output_tokens: int = 400
    tokenizers_file: Path = BACKEND_DIR / "config" / "tokenizers.yaml"
    token_calibration_sample: int = 20

    # ---- Daily refresh pipeline (see services/nightly.py, ADR 0005)
    refresh_hour: int = 1  # local time on the machine running it
    refresh_minute: int = 0
    extract_window_days: int = 7  # extract articles first seen in the last N days
    extract_max_attempts: int = 2  # transient failures: give up (and alert) after this many
    retag_lede_chars: int = 1500  # how much of the full text the topic tagger reads
    alert_source_failing_runs: int = 2  # alert when a source fails this many runs in a row
    notify_desktop: bool = True  # macOS notification when a run raises new alerts

    # ---- Full-text extraction (see enrichment/)
    extract_concurrency: int = 4
    extract_max_bytes: int = 3_000_000
    extract_min_words: int = 120

    github_token: str | None = Field(
        default=None, validation_alias=AliasChoices("OYT_GITHUB_TOKEN", "GITHUB_TOKEN")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
