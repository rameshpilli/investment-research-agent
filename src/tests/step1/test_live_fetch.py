#!/usr/bin/env python3
"""
Step 1 Test: Live Data Fetch
==============================

Hits the REAL APIs for both SOC US and AKSO NO and shows exactly what
comes back from each connector.  Run this to verify your sources are
reachable and returning useful data.

    uv run python -m src.tests.step1.test_live_fetch

What you'll see for each connector:
  - How many documents came back
  - Title, doc_type, filing_date, source_url for each document
  - First 300 chars of raw_text so you can eyeball content quality
  - Chunk count and sample chunk after chunking
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

from src.models import get_company
from src.connectors.edgar import EdgarConnector
from src.connectors.yfinance_client import YFinanceConnector
from src.connectors.web_search import WebSearchConnector
from src.connectors.newsweb import NewswebConnector
from src.connectors.company_ir import CompanyIRConnector
from src.services.corpus_store import chunk_document

_pass = 0
_fail = 0


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def check(label: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  \u2713 {label}")
    else:
        _fail += 1
        print(f"  \u2717 {label}")
        if detail:
            print(f"    \u2192 {detail}")


def show_doc(doc, index: int) -> None:
    """Print a Document's key fields in a readable format."""
    print(f"\n  [{index+1}] {doc.doc_type}: {doc.title}")
    print(f"      Filing date:  {doc.filing_date or doc.published_at or doc.as_of_date or 'n/a'}")
    print(f"      Source URL:   {doc.source_url[:100]}{'...' if len(doc.source_url) > 100 else ''}")
    print(f"      Content hash: {doc.content_hash[:16]}...")
    text_preview = doc.raw_text[:300].replace('\n', ' ').strip()
    if text_preview:
        print(f"      Text preview: {text_preview}{'...' if len(doc.raw_text) > 300 else ''}")
    else:
        print(f"      Text preview: (empty — structured_payload may have data)")
    print(f"      Text length:  {len(doc.raw_text)} chars")


def show_chunks(doc, max_show: int = 2) -> None:
    """Chunk a document and show the results."""
    chunks = chunk_document(doc)
    print(f"      Chunks:       {len(chunks)}")
    for c in chunks[:max_show]:
        preview = c.text[:150].replace('\n', ' ').strip()
        print(f"        chunk[{c.chunk_index}]: {preview}...")


async def run_connector_check(name: str, connector, profile, expect_docs: bool = True) -> list:
    """Run a single connector and display results."""
    section(f"{name} — {profile.ticker}")
    print(f"  Profile: {profile.name} ({profile.ticker})")
    print(f"  Connector: {connector.source_key} → {connector.source_name}")

    try:
        docs = await connector.fetch_documents(profile)
        check(f"Fetched {len(docs)} documents", len(docs) > 0 if expect_docs else True,
              "No documents returned" if expect_docs and not docs else "")

        for i, doc in enumerate(docs[:5]):  # show first 5
            show_doc(doc, i)
            show_chunks(doc, max_show=1)

        if len(docs) > 5:
            print(f"\n  ... and {len(docs) - 5} more documents (not shown)")

        # Summary
        print(f"\n  --- Summary ---")
        print(f"  Total documents:  {len(docs)}")
        doc_types = {}
        for d in docs:
            doc_types[d.doc_type] = doc_types.get(d.doc_type, 0) + 1
        print(f"  Doc types:        {doc_types}")
        total_text = sum(len(d.raw_text) for d in docs)
        print(f"  Total text:       {total_text:,} chars")
        total_chunks = sum(len(chunk_document(d)) for d in docs)
        print(f"  Total chunks:     {total_chunks}")

        return docs

    except Exception as e:
        check(f"{name} succeeded", False, str(e))
        import traceback
        traceback.print_exc()
        return []


async def main():
    print("\n" + "="*70)
    print("  LIVE DATA FETCH TEST — Real API calls to all connectors")
    print("  This test hits the network. Results depend on source availability.")
    print("="*70)

    soc = get_company("SOC US")
    akso = get_company("AKSO NO")

    # ── SOC US connectors ────────────────────────────────────────────────
    edgar = EdgarConnector()
    edgar_docs = await run_connector_check("SEC EDGAR", edgar, soc)
    await edgar.disconnect()

    yf = YFinanceConnector()
    yf_docs = await run_connector_check("Yahoo Finance (SOC)", yf, soc)

    ws = WebSearchConnector()
    ws_docs = await run_connector_check("DuckDuckGo Search (SOC)", ws, soc)

    # ── AKSO NO connectors ───────────────────────────────────────────────
    nw = NewswebConnector()
    nw_docs = await run_connector_check("Oslo Bors Newsweb", nw, akso)
    await nw.disconnect()

    ir = CompanyIRConnector()
    ir_docs = await run_connector_check("Aker Solutions IR Page", ir, akso)
    await ir.disconnect()

    yf2 = YFinanceConnector()
    yf2_docs = await run_connector_check("Yahoo Finance (AKSO)", yf2, akso)

    ws2 = WebSearchConnector()
    ws2_docs = await run_connector_check("DuckDuckGo Search (AKSO)", ws2, akso)

    # ── Grand summary ────────────────────────────────────────────────────
    section("GRAND SUMMARY")

    all_results = [
        ("EDGAR (SOC)",           edgar_docs),
        ("YFinance (SOC)",        yf_docs),
        ("DuckDuckGo (SOC)",      ws_docs),
        ("Newsweb (AKSO)",        nw_docs),
        ("Company IR (AKSO)",     ir_docs),
        ("YFinance (AKSO)",       yf2_docs),
        ("DuckDuckGo (AKSO)",     ws2_docs),
    ]

    print(f"\n  {'Source':<25s} {'Docs':>5s} {'Chars':>10s} {'Chunks':>7s}")
    print(f"  {'-'*25} {'-'*5} {'-'*10} {'-'*7}")
    grand_docs = 0
    grand_chars = 0
    grand_chunks = 0
    for name, docs in all_results:
        n = len(docs)
        chars = sum(len(d.raw_text) for d in docs)
        chunks = sum(len(chunk_document(d)) for d in docs)
        grand_docs += n
        grand_chars += chars
        grand_chunks += chunks
        status = "\u2713" if n > 0 else "\u2717"
        print(f"  {status} {name:<23s} {n:>5d} {chars:>10,d} {chunks:>7d}")
    print(f"  {'-'*25} {'-'*5} {'-'*10} {'-'*7}")
    print(f"  {'TOTAL':<25s} {grand_docs:>5d} {grand_chars:>10,d} {grand_chunks:>7d}")

    print(f"\n{'='*70}")
    print(f"  RESULTS: {_pass} passed, {_fail} failed")
    print(f"{'='*70}\n")

    if _fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
