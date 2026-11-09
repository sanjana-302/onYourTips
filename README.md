# OnYourTips

A personal, AI-assisted daily digest of AI advancements, software news and cool projects. It
ingests ~20 sources several times a day, dedupes and ranks them deterministically, learns
what you care about from what you save and dismiss, and (next) summarizes selected articles
with an LLM, with citations and a visible per-summary cost.

**Status:** ingestion, ranking, weekly digest, interaction tracking, a FastAPI backend, a mobile
web app, and the AI foundation: an LLM gateway with a cost ledger (local models via Ollama) and
full-text extraction. Next: summaries with citations (roadmap feature #3).

## Repository layout

```
backend/            Python package `onyourtips` (uv): ingestion, ranking, storage, services, CLI
  src/onyourtips/   core · ingestion · ranking · storage · services · api · enrichment · llm
  config/           sources.yaml · taxonomy.yaml
  tests/            unit · integration
  scripts/          install_schedule.sh (macOS launchd)
frontend/           mobile web app: React + Vite + TypeScript
docs/               architecture.md · adr/ (design decisions)
.github/workflows/  CI: lint + tests
                    * = planned
```

## Use it on your phone

Requires [uv](https://docs.astral.sh/uv/), Python ≥ 3.10 and Node ≥ 20 (`brew install node`).

```bash
cd backend && uv sync                              # 1. backend deps
cd ../frontend && npm install && npm run build     # 2. build the web app
cd ../backend && uv run oyt ingest                 # 3. first batch of stories (~1 min)
uv run oyt serve                                   # 4. prints: On your phone: http://192.168.x.x:8000
```

5. On your phone, on the **same Wi-Fi**, open the printed address.
6. iPhone: Share → **Add to Home Screen**. Android/Chrome: menu → **Install app**. It then opens
   full-screen like a native app.

If macOS asks whether Python may accept incoming connections, choose **Allow**. There's no login,
so only run `oyt serve` on a network you trust (your home Wi-Fi).

**While developing the UI:** run `uv run oyt serve --reload` in one terminal and `npm run dev` in
`frontend/` in another, then open `http://<your-ip>:5173` on your phone. Changes appear
instantly.

## Daily refresh

Once a day at **1:00 AM** (your Mac's local time) the app refreshes on its own (ADR 0005):
ingest the best of the last 24 hours → full text + passages for every new story (no AI) →
retry yesterday's timeouts → re-tag with the full text → raise alerts.

```bash
cd backend
./scripts/install_schedule.sh          # install the 1:00 AM job (macOS launchd)
uv run oyt nightly --trigger manual    # run the full refresh now
uv run oyt alerts                      # failing sources, pages given up on
tail -f data/logs/nightly.log          # what the 1:00 AM run printed
```

The Refresh button in the app runs the same pipeline and is recorded as a manual refresh.
Problems appear under **Needs attention** in the You tab (and as a macOS notification).

## AI setup (local models with Ollama)

```bash
brew install ollama          # or download the app from ollama.com
ollama serve                 # leave running (the Ollama app does this for you)
ollama pull llama3.2:3b      # default model, about 2 GB

cd backend
uv run oyt llm status        # is Ollama running, which models are downloaded?
uv run oyt llm ping "What is retrieval-augmented generation?"
uv run oyt costs             # calls, tokens, speed and cost from the ledger
```

**Compare models** to see how they differ and improve. Each run saves a report in
`backend/data/compare/`:

```bash
ollama pull qwen3.5:4b && ollama pull granite4.2:3b
uv run oyt llm compare -m llama3.2:3b -m qwen3.5:4b -m granite4.2:3b
uv run oyt llm compare -m llama3.2:3b -m granite4.2:3b --structured   # JSON reliability
uv run oyt llm compare -m llama3.2:3b -m qwen3.5:4b --article 3f9a1c2e
```

**Token counts:** how many tokens a summary of each article would use. Counted every night with
the model's own tokenizer (Hugging Face `tokenizers`, downloaded once), with no model call;
check them against Ollama's real counts with `calibrate`:

```bash
uv run oyt tokens                       # typical / high-end / largest article, over the limit
uv run oyt tokens calibrate --sample 20 # measure 20 articles with Ollama (one short call each)
uv run oyt tokens estimate              # recompute all estimates now
```

**Summaries with citations** are made every night for every new story (until 08:30), or
when you tap **Summarize with AI** on a story:

```bash
uv run oyt summarize 3f9a1c2e           # one story (cached); --plan shows tokens + cost only
uv run oyt summaries                    # cost: success, tokens, laptop time, projections
uv run oyt summaries list               # attempts: done yes/no, reason, estimated vs real
```

**Evals** (see [the field guide](docs/evals-field-guide.md)):

```bash
uv run oyt eval golden                  # pick ~20 stories
uv run oyt eval label                   # judge each claim yourself
uv run oyt eval judge -j qwen3.5:4b     # a second model judges the same claims
uv run oyt eval agreement -j qwen3.5:4b # can it be trusted? (Cohen's kappa)
uv run oyt eval compare -m llama3.2:3b -m qwen3.5:4b -j granite4.2:3b
```

**Full text** is extracted automatically every night. To extract specific stories now:

```bash
uv run oyt extract --digest          # this week's picks (also: --saved, or article ids)
uv run oyt extract --show 3f9a1c2e   # read the passages of one story
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.10.

```bash
make install        # uv sync in backend/
make test           # ruff + pytest (offline, HTTP mocked)
make ingest         # fetch all sources → backend/data/onyourtips.db
make digest         # best of the last 7 days
make serve          # API + web app on your network
make schedule       # macOS: daily refresh at 1:00 AM (oyt nightly)
```

Daily use (from `backend/`, or via `uv run --directory backend oyt …`):

```bash
uv run oyt feed                     # what's new today
uv run oyt digest                   # best of the week, grouped by type
uv run oyt open  3f9a1c2e           # open in browser + record it
uv run oyt save  3f9a1c2e -m "weekend idea"
uv run oyt rate  3f9a1c2e 5
uv run oyt dismiss 77b0e4d1
uv run oyt saved --days 7
uv run oyt interests
```

## Design

- [Architecture](docs/architecture.md): pipeline, data model, ranking, digest
- [Design log book](docs/design-logbook.md): where we deviated from the original design, and why
- [ADR 0001](docs/adr/0001-repo-structure-and-storage.md): repo layout, SQLite, migrations
- [ADR 0002](docs/adr/0002-summaries-and-citations.md): LLM summaries with passage citations
- [ADR 0003](docs/adr/0003-llm-cost-ledger.md): LLM gateway and cost ledger
- [ADR 0004](docs/adr/0004-local-models-first.md): local models first (Ollama)
- [ADR 0005](docs/adr/0005-daily-refresh-pipeline.md): daily 1 AM refresh, full text for every new story, retries and alerts
- [ADR 0006](docs/adr/0006-summaries-every-story-and-evals.md): summaries for every story, map-reduce, four eval layers
- [Evals field guide](docs/evals-field-guide.md): eval jargon, applied to our summaries
- [AI roadmap](docs/OnYourTips-AI-Roadmap.xlsx): the 13 AI features in learning order, with scaling notes

## Roadmap

1. ✅ Ingestion core: 20 sources, dedupe, tagging, ranking
2. ✅ Weekly digest, interaction log, scheduled ingest
3. ✅ LLM gateway + cost ledger + limits + `oyt costs`, local models via Ollama, `oyt llm compare`
4. ✅ Full-text extraction → numbered passages, nightly for every new story (`oyt nightly`)
5. ✅ LLM summaries with citations, every story nightly, map-reduce, evals
6. ✅ FastAPI: `/feed`, `/digest`, `/articles/{id}`, `/interactions`, `/saved`, `/interests`, `/status`, `/ingest`
7. ✅ Mobile web app v1 (Today, Week, Saved, You, summary-first article view)
8. ⏳ Evals in CI (citation validity, faithfulness), cost optimizations
9. ⏳ Semantic layer: embeddings, story clustering, weekend-project recommender
