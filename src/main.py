"""
CLI Entry Point
================

Two-persona workflow:

    Data Engineer:
        python -m src.main --fetch "SOC US"       # one company
        python -m src.main --fetch-all             # all companies

    AI Engineer:
        python -m src.main --ingest "SOC US"      # one company
        python -m src.main --ingest-all            # all companies

    Research Pipeline:
        python -m src.main "SOC US"               # 3-phase research

    Follow-up:
        python -m src.main "SOC US" --question "What are the risks?"

    Daily schedule (cron):
        0 18 * * 1-5  cd /path/to/project && uv run python3 -m src.main --fetch-all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from src.pipeline import answer_question, close_pipeline_resources, run_pipeline_sync


def _render_summary(result: dict[str, object]) -> str:
    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    lines = [
        f"Ticker: {summary.get('ticker', 'unknown')}",
        f"Success: {summary.get('success', False)}",
    ]
    if summary.get("corpus_status"):
        lines.append(f"Corpus: {summary['corpus_status']}")
    if summary.get("verdict"):
        lines.append(f"Verdict: {summary['verdict']}")

    outputs = summary.get("outputs", {})
    if isinstance(outputs, dict):
        lines.extend(
            [
                "",
                "Outputs:",
                f"- Phase 1: {outputs.get('phase1') or 'not produced'}",
                f"- Phase 2: {outputs.get('phase2') or 'not produced'}",
                f"- Phase 3: {outputs.get('phase3') or 'not produced'}",
            ]
        )
    return "\n".join(lines)


def _run_fetch(ticker: str) -> None:
    """Step 1 (Data Engineer): Fetch raw data and save to data/raw/."""
    from src.models import get_company
    from src.services.corpus_store import CorpusStore
    from src.services.raw_store import list_raw_summary, raw_company_dir
    from src.steps.ingest import fetch_corpus

    profile = get_company(ticker)
    store = CorpusStore()
    output_dir = raw_company_dir(profile)

    print(f"Fetching: {profile.name} ({profile.ticker})")
    print(f"  Sources: {', '.join(profile.enabled_connectors)}")

    try:
        _, status = asyncio.run(fetch_corpus(profile, store))
    finally:
        store.close()

    # Show summary
    summary = list_raw_summary(profile)
    folders = summary.get("folders", {})
    total = summary.get("total_files", 0)
    folder_str = ", ".join(f"{f}({c})" for f, c in sorted(folders.items()))
    print(f"  Result:  {status} — {total} files [{folder_str}]")
    print(f"  Saved:   {output_dir}/")
    print()


def _run_ingest(ticker: str) -> None:
    """Step 2 (AI Engineer): Build corpus from raw files."""
    from src.models import get_company
    from src.services.corpus_store import CorpusStore
    from src.services.raw_store import raw_company_dir
    from src.steps.ingest import ingest_corpus

    profile = get_company(ticker)
    store = CorpusStore()

    print(f"Ingesting: {profile.name} ({profile.ticker})")
    print(f"  Input:     {raw_company_dir(profile)}/")
    print(f"  Embedding: {store.embedding_service.provider} ({store.embedding_service.dimensions}d)")

    try:
        snapshot, status = asyncio.run(ingest_corpus(profile, store))
    finally:
        store.close()

    print(f"  Result:    {status} — {len(snapshot.documents)} docs, {len(snapshot.chunks)} chunks")
    print(f"  Saved:     {store.corpus_dir(profile)}/")
    print()


def _run_all(step_fn, label: str) -> None:
    """Run a step for all configured companies."""
    from src.models.companies import list_supported_tickers

    tickers = list_supported_tickers()
    print(f"{'='*60}")
    print(f"{label} — {len(tickers)} companies")
    print(f"{'='*60}")
    print()

    for ticker in tickers:
        try:
            step_fn(ticker)
        except Exception as exc:
            print(f"  ERROR: {ticker} — {exc}")
            print()

    print(f"{'='*60}")
    print(f"Done. {len(tickers)} companies processed.")
    print(f"{'='*60}")


def main() -> None:
    # Initialize logging so INFO/WARNING traces from corpus_store, embeddings,
    # mcp_server, etc. are visible during CLI runs.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Investment research pipeline.",
        epilog=(
            "Workflow:\n"
            "  Step 1: --fetch        Fetch one company  → data/raw/\n"
            "          --fetch-all    Fetch all companies → data/raw/\n"
            "  Step 2: --ingest       Ingest one company  → data/processed/\n"
            "          --ingest-all   Ingest all companies → data/processed/\n"
            "  Run:    <ticker>       Execute 3-phase research pipeline\n"
            "  Q&A:    --question     Ask follow-up against corpus\n"
            "\n"
            "Daily schedule (cron — after market close):\n"
            "  0 18 * * 1-5  uv run python3 -m src.main --fetch-all\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("ticker", nargs="?", default=None,
                        help="Company ticker (e.g. 'SOC US', 'AKSO NO')")
    parser.add_argument("--fetch", action="store_true",
                        help="Fetch raw data for one company → data/raw/")
    parser.add_argument("--fetch-all", action="store_true",
                        help="Fetch raw data for ALL companies → data/raw/")
    parser.add_argument("--ingest", action="store_true",
                        help="Build corpus for one company → data/processed/")
    parser.add_argument("--ingest-all", action="store_true",
                        help="Build corpus for ALL companies → data/processed/")
    parser.add_argument("--question", help="Ask a follow-up question against the stored corpus.")
    parser.add_argument("--json", action="store_true", help="Print the full raw result as JSON.")
    args = parser.parse_args()

    # --fetch-all / --ingest-all don't need a ticker
    if args.fetch_all:
        _run_all(_run_fetch, "Step 1: Fetching all companies")
        return

    if args.ingest_all:
        _run_all(_run_ingest, "Step 2: Ingesting all companies")
        return

    # Everything else needs a ticker
    if not args.ticker:
        parser.error("ticker is required (or use --fetch-all / --ingest-all)")

    if args.fetch:
        _run_fetch(args.ticker)
        return

    if args.ingest:
        _run_ingest(args.ticker)
        return

    if args.question:
        try:
            answer = asyncio.run(answer_question(args.ticker, args.question))
            print(answer)
        finally:
            close_pipeline_resources()
        return

    result = run_pipeline_sync(args.ticker)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return
    print(_render_summary(result))


if __name__ == "__main__":
    main()
