# onyourtips (backend)

Python package for OnYourTips: ingestion, ranking, storage, services and the `oyt` CLI.
See the [root README](../README.md) and [docs/architecture.md](../docs/architecture.md).

```bash
uv sync                    # install
uv run pytest              # tests (offline)
uv run ruff check . && uv run ruff format --check .
uv run oyt --help
```

- Config: `config/sources.yaml` (what to ingest), `config/taxonomy.yaml` (topic rules)
- Settings: `OYT_*` environment variables or `backend/.env` (see `.env.example`)
- Data: `data/onyourtips.db` (SQLite; schema migrations in `src/onyourtips/storage/migrations/`)
- Adding a source: an `rss` entry in `sources.yaml`, or a `Source` subclass in
  `src/onyourtips/ingestion/sources/` registered with `@register`
