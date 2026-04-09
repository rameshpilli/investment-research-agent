# Scaling Guide

This note explains how I would scale this project beyond the two-company case study.

## What I Would Keep

I would keep the same split:

1. a deterministic corpus-prep layer
2. a tool-using AI research layer

That split is worth keeping because data collection and AI reasoning change at different speeds.

## How I Would Scale Ingestion

For hundreds or thousands of companies, I would move corpus prep into a job system:

- one queue for source fetch jobs
- one queue for parsing and chunking jobs
- one queue for embedding and indexing jobs
- one manifest per company to track freshness, failures, and the last good run

I would also split connectors into two groups:

- slow-changing sources: annual reports, quarterly reports, filings
- fast-changing sources: news, prices, and short market updates

That keeps refresh costs under control.

## How I Would Handle Parked Ideas

For parked ideas, I would not rerun the full research pipeline every day.

I would do this instead:

1. watch the sources passively
2. detect material changes
3. trigger a focused re-run only when something important changes

Examples of material changes:

- a new 10-Q or annual report
- a regulatory announcement
- guidance cuts or major contract wins
- large price moves with matching news

The analyst should get a short alert first, not a full dossier every time.

## Model and Vendor Flexibility

I would keep three separate interfaces:

1. chat model interface
2. embedding interface
3. retrieval interface

That way:

- Claude, GPT, or another chat model can be swapped without changing the pipeline
- OpenAI-compatible embeddings, Voyage, or another provider can be swapped without changing the store
- Qdrant can later be replaced without changing the prompts

## Cost Shape

For a bigger system, the main cost buckets are:

- source fetching
- PDF parsing
- embeddings
- LLM calls for writing and follow-up answers

The cheapest way to control cost is:

- reuse the corpus
- refresh only stale sources
- keep the LLM out of Step 1
- use smaller models for gap analysis and follow-up when possible
- save generated outputs so analysts do not rerun the same work

## Security and Compliance

At minimum, I would add:

- audit logs for who ran what
- secrets management instead of plain `.env` in production
- source allowlists for outbound fetches
- role-based access to research outputs
- PII and restricted-data checks before content is sent to external models
- vendor approval rules for external AI APIs

For a finance setting, I would also keep strong source traceability on every important claim.

## Where I Would Avoid AI

I would avoid AI in places where deterministic behavior matters more than style:

- source fetching
- parsing
- document normalization
- refresh decisions based on known rules
- citation verification

These should stay as plain code.

AI is best used for:

- gap reasoning
- contradiction spotting
- drafting the dossier
- answering grounded follow-up questions

## Known Weaknesses Today

These are the honest weak points in the current repo:

1. It is still a case-study build, not a production service.
2. Source coverage is narrow and tailored to two companies.
3. The local embedding fallback is useful offline, but not the best retrieval choice for live research.
4. Public-source scraping can break when source sites change layout.
5. Prompt quality and verdict quality still depend on the chosen model.

## What I Would Do Next

If I had more time, my next moves would be:

1. add queue-backed ingestion and retry policies
2. add passive monitoring with alert thresholds
3. add real embedding defaults for live runs
4. add stronger output evaluation and regression tests
5. add analyst feedback loops so the system learns what evidence was actually useful

## Phase 4 Thoughts: Financial Model Extraction

If I were adding the optional fourth phase, I would not start with a general PDF-to-spreadsheet prompt.

I would do it in this order:

1. **Use structured filings first**
  For SEC companies like SOC, start with XBRL before touching plain PDF or HTML tables.
2. **Use table extraction second**
  For sources without clean XBRL, extract report tables and map them into a standard financial schema.
3. **Normalize into a model-ready layer**
  Store values like revenue, EBITDA, capex, debt, cash, and shares outstanding in structured rows with period labels.
4. **Keep citations on every number**
  Every extracted value should point back to the filing, page, table, and text snippet it came from.
5. **Add review rules**
  Flag large changes, unit mismatches, and missing line items for human review.

For these two companies, the likely best path is:

- **SOC US:** XBRL first, because the SEC path is much cleaner.
- **AKSO NO:** annual report and quarterly report table extraction, because the source is more PDF-heavy.

The key design rule is simple:

- the AI can help map and label numbers
- the final stored model should still be deterministic, reviewable, and source-linked

