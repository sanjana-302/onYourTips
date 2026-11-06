# ADR 0001 — Monorepo layout, one backend package, SQLite with versioned migrations

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

OnYourTips is moving from a working ingestion core to an AI-enriched product: LLM summaries with
citations, FastAPI endpoints and a first UI. It's a solo project, built partly to learn how
production AI apps are put together, so the structure should look like a real production codebase
without the operational weight of one.

The schema is about to grow a lot (extracted content, passages, summaries, an LLM cost ledger).
Until now, tables were created by one inline `CREATE TABLE IF NOT EXISTS` script, which cannot
alter existing tables.

## Decision

1. **Monorepo:** `backend/` (Python, uv), `frontend/` (React + Vite + TypeScript, later),
   `infra/` (Docker, later), `docs/` (architecture + ADRs), `.github/workflows/` (CI).
2. **One backend package, `onyourtips`, with clear internal layers. No microservices.**
   The CLI, the scheduled ingest worker and the API are separate entry points into the same code.

   | Module | Responsibility | May import |
   |---|---|---|
   | `core` | models, settings, HTTP client, utils | nothing internal |
   | `ingestion` | connectors, normalize, tagging, pipeline | core, storage |
   | `ranking` | signal, recency score, digest selection — pure functions | core |
   | `storage` | SQLite repository + migrations | core, ranking.scoring |
   | `enrichment` | text extraction, passages, summaries *(planned)* | core, storage, llm |
   | `llm` | the **only** code that calls LLM providers *(planned)* | core, storage |
   | `services` | use cases (`build_digest`, `summarize_article`, …) | everything above |
   | `api`, `cli` | thin adapters: parse input → call services → format output | services, core |

   Rule: `api` and `cli` call `services` only, never storage directly.
3. **SQLite first.** Zero ops, one file, enough for one user with many thousands of articles.
   All SQL lives in `storage/`, so moving to Postgres later is contained there.
4. **Forward-only numbered SQL migrations** in `storage/migrations/NNNN_name.sql`, tracked with
   `PRAGMA user_version` and applied automatically when the database is opened. Each runs in a
   transaction. A shipped migration is never edited; changes go in a new file.
   Migrations 0001–0002 use `IF NOT EXISTS` so databases created before this change are adopted.
5. **Tooling:** uv for environments, ruff for lint + format, pytest split into `unit/` (pure, fast),
   `integration/` (DB, mocked HTTP) and later `evals/` (LLM output quality).

## Alternatives considered

- **SQLAlchemy + Alembic now:** the standard choice, and the one to adopt when moving to Postgres
  or multiple writers. Today it would add an ORM layer over ~400 lines of clear SQL for no
  user-visible gain. Revisit at the Postgres move (see Consequences).
- **Separate services for ingestion, API and LLM worker:** better isolation, but far more
  deployment and debugging cost for a single user. The module boundaries above keep the split
  possible later.
- **Postgres from day one:** needs Docker running for every test and dev session. Deferred.

## Consequences

- One `uv sync`, one test command, one deployable. Easy to run locally and in CI.
- Each new table or column is one SQL file and one test.
- Triggers to revisit: a hosted multi-user deployment, concurrent writers (API + worker under
  load), or full-text/vector search needs → Postgres (+ pgvector) with Alembic.
