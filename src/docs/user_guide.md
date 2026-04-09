# User Guide

## Prerequisites

For Docker-first usage (recommended):

- Docker Desktop (or Docker Engine + Compose)
- access to ports `8000`, `8081`, and `6333`

For local (non-Docker) usage:

- Python `3.11+`
- `uv`

## Overview

This project is organized around three layers:

1. raw source collection under `src/data/raw/`
2. processed corpus metadata in SQLite plus vectors in Qdrant
3. Claude research phases that access the corpus through the MCP server

The default runtime story is MCP-first, not REST-first.

## Supported Companies

- `SOC US`
- `AKSO NO`

## Fastest Start

If you just want to run the product as a user:

```bash
cp src/.env.example src/.env
docker compose up --build
```

Before starting services, open `src/.env` and fill any required values: at minimum the SEC user agent, and for best results **Claude** (`ANTHROPIC_API_KEY`) plus an **embedding provider** (OpenRouter, OpenAI, or another OpenAI-compatible host—see *Embeddings (vector search)* below).

Then:

1. Open `http://localhost:8000`
2. Type `SOC US` or `AKSO NO`
3. Wait for the reports to render
4. Ask follow-up questions in the same chat
5. Type `rerun SOC US` if you want to regenerate instead of loading cached outputs

Notes:

- First run can take longer while images build and the corpus initializes.
- In Docker, generated markdown reports are persisted to the host under `src/output/`.

## Setup

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .
cp src/.env.example src/.env
```

Then edit `src/.env` with your environment-specific settings and secrets before running the app.

Minimum env:

```env
RESEARCH_USER_AGENT_NAME=investment-research/0.1
RESEARCH_USER_AGENT_EMAIL=your.email@example.com
```

Optional MCP-first multi-service env:

```env
RESEARCH_QDRANT_URL=http://localhost:6333
RESEARCH_MCP_URL=http://localhost:8081/mcp
RESEARCH_USE_LLM=true
ANTHROPIC_API_KEY=sk-ant-...
```

If `RESEARCH_USE_LLM=false` or the API key is missing, the system still runs, but it produces deterministic fallback reports instead of Claude-written analysis.

### Embeddings (vector search)

**A real embedding service matters for quality:** it is what makes vector search “understand” your questions and filings in a semantic way, so Phase 1–3 and `search_corpus` tend to pull **better-matched** chunks.

**If you have no embedding API (no keys):** the app still runs. It automatically uses **local hash embeddings**—deterministic, offline, no extra bill—but retrieval is closer to **token overlap** than true semantics, so ranked results are often **less accurate** than with a hosted model. For demos or air-gapped use that can be fine; for analyst-grade search, configure an embedding provider.

**Why technically:** Each chunk is converted into a vector so Qdrant can score passages by **similarity** during **ingest** and whenever tools like **`search_corpus`** run, instead of relying only on shared keywords.

**What to provide:** Point the app at any **OpenAI-style `/v1/embeddings` API** and supply credentials. Typical variables in `src/.env`:

| Variable | Purpose |
| -------- | ------- |
| `RESEARCH_EMBEDDING_PROVIDER` | Use `openai_compatible` for OpenAI, OpenRouter, Azure OpenAI, or other compatible hosts. |
| `RESEARCH_EMBEDDING_BASE_URL` | Base URL for the embeddings API (e.g. `https://api.openai.com/v1` or `https://openrouter.ai/api/v1`). |
| `RESEARCH_EMBEDDING_MODEL` | Model id the host expects (e.g. `text-embedding-3-small` or `openai/text-embedding-3-small`). |
| `RESEARCH_EMBEDDING_DIMENSIONS` | Vector size for that model (must match the model and Qdrant collection—see `.env.example`). |
| `RESEARCH_EMBEDDING_API_KEY` | Dedicated key (optional if you use a global key below). |

Authentication is resolved in this order: **`RESEARCH_EMBEDDING_API_KEY`**, then **`OPENROUTER_API_KEY`**, then **`OPENAI_API_KEY`**.

**If you skip keys:** Same as above—**local hash fallback**: ingest and search keep working, but expect **weaker or noisier** relevance ranking than with a real embedding API.

See `src/.env.example` for a ready-made OpenRouter-oriented block.

## CLI Workflow

Fetch raw data:

```bash
uv run python -m src.main --fetch "SOC US"
uv run python -m src.main --fetch-all
```

Build the corpus:

```bash
uv run python -m src.main --ingest "SOC US"
uv run python -m src.main --ingest-all
```

Run the three-phase workflow:

```bash
uv run python -m src.main "SOC US"
```

Reports are written to:

- `src/output/{slug}/phase1_ingestion_report.md`
- `src/output/{slug}/phase2_dossier.md`
- `src/output/{slug}/phase3_analyst_brief.md`

Ask a follow-up question:

```bash
uv run python -m src.main "SOC US" --question "What are the main risks?"
```

## Chainlit UI Workflow

After the UI opens:

1. Enter `SOC US` or `AKSO NO`
2. Review the three generated markdown reports
3. Ask follow-up questions in the same conversation
4. Use `rerun <ticker>` if you want to force a fresh run

If cached reports already exist, the UI loads them immediately. If not, it runs the full pipeline and shows phase/tool progress.

## Docker Runtime

Recommended stack:

```bash
docker compose up --build
```

Services:

- UI: `http://localhost:8000`
- MCP: `http://localhost:8081/mcp`
- Qdrant: `http://localhost:6333`

## Local MCP-First Runtime

Terminal 1:

```bash
docker compose up qdrant
```

Terminal 2:

```bash
RESEARCH_QDRANT_URL=http://localhost:6333 \
  uv run python -m src.services.mcp_http
```

Terminal 3:

```bash
RESEARCH_MCP_URL=http://localhost:8081/mcp \
RESEARCH_QDRANT_URL=http://localhost:6333 \
  uv run chainlit run src/app.py --host 0.0.0.0 --port 8000
```

## What To Ask

The best prompts are grounded research questions about a single loaded company.

Examples:

- `What are the main risk factors?`
- `What is the current verdict and why?`
- `What evidence supports the downside case?`
- `What changed between the latest filings or announcements?`
- `What important materials are still missing?`
- `Summarize the valuation context in plain English.`
- `What would change the verdict from Needs More Info to Proceed?`
- `What are the most relevant recent announcements for this company?`

Less effective prompts are open-ended requests that need internet access beyond the stored corpus.

## External MCP Clients

If you use Claude Desktop, Cursor, or another MCP client directly against `src/services/mcp_http.py`:

- pass `ticker` on company-scoped tools, or
- send the `X-Research-Ticker` header

The server now rejects requests that omit the company context instead of silently choosing one.

## Data Layout

Raw source-of-truth files:

- `src/data/raw/{slug}/sec_filings/...`
- `src/data/raw/{slug}/company_reports/...`
- `src/data/raw/{slug}/news/...`
- `src/data/raw/{slug}/market_data/...`

Processed metadata:

- `src/data/processed/{slug}/store.db`
- `src/data/processed/{slug}/manifest.json`

Outputs:

- `src/output/{slug}/phase1_ingestion_report.md`
- `src/output/{slug}/phase2_dossier.md`
- `src/output/{slug}/phase3_analyst_brief.md`

## Inspect SQLite Corpus (Optional)

If you want to inspect the processed corpus directly:

List tables:

```bash
sqlite3 src/data/processed/soc_us/store.db ".tables"
```

Preview sample 10-K text:

```bash
sqlite3 -header -column src/data/processed/soc_us/store.db "SELECT substr(raw_text,1,300) AS sample_text FROM documents WHERE doc_type='10-K' ORDER BY filing_date DESC LIMIT 1;"
```

Count documents and chunks:

```bash
sqlite3 src/data/processed/soc_us/store.db "SELECT COUNT(*) AS document_count FROM documents;"
sqlite3 src/data/processed/soc_us/store.db "SELECT COUNT(*) AS chunk_count FROM chunks;"
```

## Troubleshooting

- If **`search_corpus`** or ingest seems blind to obvious phrases, confirm embedding env vars and keys match your provider, then **re-ingest** so vectors are rebuilt with the same dimensions as `RESEARCH_EMBEDDING_DIMENSIONS`.
- If the UI loads old reports, type `rerun SOC US` or `rerun AKSO NO`.
- If follow-up answers say the question cannot be answered, the current corpus likely lacks the needed source material.
- If Docker is running, the expected endpoints are UI `:8000`, MCP `:8081/mcp`, and Qdrant `:6333`.
- If you want deterministic runs without Anthropic, set `RESEARCH_USE_LLM=false`.

## Packaging

This repo uses `pyproject.toml` and `uv.lock` as the canonical packaging and dependency definition.

## Legacy Compatibility

`src/api.py` is retained as an optional compatibility layer. It is not the primary interface in the default architecture or docker runtime.

## Tests

```bash
./.venv/bin/python -m src.tests.step1.test_models
./.venv/bin/python -m src.tests.step1.test_corpus_store
./.venv/bin/python -m src.tests.step2.test_mcp_tools
./.venv/bin/python -m src.tests.step2.test_phases
./.venv/bin/python -m src.tests.step2.test_pipeline
./.venv/bin/python -m src.tests.step2.test_followup
```

