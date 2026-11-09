# Contributing to OnYourTips

Thanks for your interest! Issues and pull requests are welcome, from typo fixes to new sources.

## Set up

```bash
cd backend && uv sync          # Python ≥ 3.10, https://docs.astral.sh/uv/
cd ../frontend && npm install  # Node ≥ 20
```

## Before you open a pull request

```bash
cd backend
uv run ruff format . && uv run ruff check .
uv run pytest -q               # offline: HTTP and the LLM are faked, no Ollama needed

cd ../frontend
npm run build                  # type-checks and builds the app
```

CI runs the same checks on Python 3.10 and 3.12.

## Where things go

- **Code layers** (see [ADR 0001](docs/adr/0001-repo-structure-and-storage.md)):
  `api` and `cli` only call `services`. Only `llm/providers.py` talks to model servers. All
  SQL lives in `storage/`.
- **Schema changes:** add a new numbered file in `backend/src/onyourtips/storage/migrations/`.
  Never edit a shipped migration. Describe new tables and columns in
  [docs/data-dictionary.md](docs/data-dictionary.md); a test fails until you do.
- **Bigger decisions:** add an ADR in `docs/adr/` (copy the format of an existing one).

## Common contributions

**Add an RSS source.** Add an entry to `backend/config/sources.yaml`:

```yaml
- id: my-blog
  kind: rss
  name: My Blog
  content_type: blog_post
  weight: 1.5
  params: { url: https://example.com/feed.xml }
```

**Add another kind of source.** Subclass `Source` in `backend/src/onyourtips/ingestion/sources/`,
register it with `@register`, and add a test with a recorded response (see
`tests/integration/test_sources.py`).

**Add or tune topics.** Edit the keywords in `backend/config/taxonomy.yaml`. The tests in
`tests/unit/test_tagging_scoring.py` show how scoring works.

**Change a prompt.** Prompts live in `backend/src/onyourtips/enrichment/summary_prompt.py` and
`services/evals.py`. Bump the version string (`summary-v1` to `summary-v2`) whenever the text
changes: summaries, token counts and eval results are stored per version, so old and new can be
compared. Run `oyt eval compare` on the golden set before and after, and include the numbers in
the pull request. See the [evals field guide](docs/evals-field-guide.md).

## Reporting bugs

Please include the command you ran, its output, your OS and Python version, and (for summary
problems) the model name and `oyt summaries list` output.
