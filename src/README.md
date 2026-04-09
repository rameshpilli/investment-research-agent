# Investment Research Platform

This package implements the case-study workflow for:

- `SOC US` — Sable Offshore
- `AKSO NO` — Aker Solutions

## Architecture

```
Analyst question
  → Embed into 256-dim vector
  → Qdrant: cosine similarity → returns chunk_ids + scores
  → SQLite: SELECT * FROM chunks WHERE chunk_id IN (...) → returns full text
  → Claude (or deterministic fallback) → cited answer
```

Two databases, one retrieval:
- **Qdrant** (vector DB) — finds *which* chunks are relevant via cosine similarity
- **SQLite** (`store.db`) — provides *what's in them*: full text, document metadata, filing dates

The 8 MCP tools (`search_corpus`, `list_documents`, `get_document`, `get_filing_section`, `compare_filings`, `get_missing_materials`, `get_corpus_status`, `list_available_companies`) access both databases and are exposed two ways:
- **In-process** via Claude Agent SDK (used by the pipeline)
- **Over HTTP** via Streamable HTTP MCP server (used by Claude Desktop, Cursor, etc.)

## What Runs

1. `--fetch`
   Pulls public materials into `src/data/raw/{slug}/`
2. `--ingest`
   Builds `store.db`, `manifest.json`, and vector state from the raw corpus
3. Research pipeline
   Runs:
   - Phase 1 ingestion report
   - Phase 2 deep research dossier
   - Phase 3 analyst brief
   - follow-up Q&A grounded in the stored corpus

## Data Layout

Raw files:

- `src/data/raw/{slug}/sec_filings/...`
- `src/data/raw/{slug}/company_reports/...`
- `src/data/raw/{slug}/news/...`
- `src/data/raw/{slug}/market_data/...`

Processed corpus:

- `src/data/processed/{slug}/store.db`
- `src/data/processed/{slug}/manifest.json`
- vectors in shared Qdrant

Outputs:

- `src/output/{slug}/phase1_ingestion_report.md`
- `src/output/{slug}/phase2_dossier.md`
- `src/output/{slug}/phase3_analyst_brief.md`

## Setup

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp src/.env.example src/.env
```

Minimum useful env:

```env
RESEARCH_USER_AGENT_NAME=investment-research/0.1
RESEARCH_USER_AGENT_EMAIL=your.email@example.com
```

**Embeddings:** A hosted embedding API (OpenAI-compatible: `RESEARCH_EMBEDDING_*` plus `RESEARCH_EMBEDDING_API_KEY`, `OPENROUTER_API_KEY`, or `OPENAI_API_KEY`) is **important for good retrieval**—it drives semantic chunk ranking in Qdrant. If none of those keys are set, the stack still works using **local hash embeddings** (offline, no extra service), but results are usually **less accurate** than with a real model. See `src/docs/user_guide.md` → *Embeddings (vector search)*.

For the full MCP-first multi-service runtime:

```env
RESEARCH_MCP_URL=http://localhost:8081/mcp
RESEARCH_QDRANT_URL=http://localhost:6333
RESEARCH_USE_LLM=true
ANTHROPIC_API_KEY=sk-ant-...
```

If `RESEARCH_USE_LLM=false` or the API key is absent, the system falls back to deterministic markdown outputs.

## Run

### Docker

```bash
docker compose up --build
```

Then:

1. Open `http://localhost:8000`
2. Type `SOC US` or `AKSO NO`
3. Wait for the reports to load
4. Ask follow-up questions in the same chat
5. Use `rerun SOC US` if you want to force a fresh run instead of loading cached reports

### Local

Terminal 1:

```bash
docker compose up qdrant
```

Terminal 2:

```bash
RESEARCH_QDRANT_URL=http://localhost:6333 uv run python -m src.services.mcp_http
```

Terminal 3:

```bash
RESEARCH_MCP_URL=http://localhost:8081/mcp RESEARCH_QDRANT_URL=http://localhost:6333 \
  uv run chainlit run src/app.py --host 0.0.0.0 --port 8000
```

### CLI

Offline deterministic:

```bash
RESEARCH_USE_LLM=false ANTHROPIC_API_KEY= ./.venv/bin/python -m src.main "SOC US"
```

Claude over MCP:

```bash
RESEARCH_MCP_URL=http://localhost:8081/mcp RESEARCH_QDRANT_URL=http://localhost:6333 \
  uv run python -m src.main "SOC US"
```

## Asking Questions

The system works best when the question is anchored to the ingested corpus for a single company.

Good examples:

- `What are the main risk factors?`
- `What supports the current verdict?`
- `What changed between the latest filings?`
- `What materials are still missing?`
- `Summarize the valuation context for the PM.`
- `What would change this from Needs More Info to Proceed?`

In the UI, ask these after the ticker has been loaded in the same chat.

For CLI follow-up:

```bash
uv run python -m src.main "SOC US" --question "What evidence supports the verdict?"
```

For external MCP clients, company-scoped tools must include a `ticker` argument or send `X-Research-Ticker`.

## Packaging

`pyproject.toml` and `uv.lock` are the canonical dependency and environment files for this repo.

## Tests

Useful smoke checks:

```bash
./.venv/bin/python -m src.tests.step1.test_models
./.venv/bin/python -m src.tests.step1.test_corpus_store
./.venv/bin/python -m src.tests.step2.test_mcp_tools
./.venv/bin/python -m src.tests.step2.test_phases
./.venv/bin/python -m src.tests.step2.test_pipeline
./.venv/bin/python -m src.tests.step2.test_followup
```
