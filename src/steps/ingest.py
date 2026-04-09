"""
Corpus Ingestion — Two-persona model
======================================

**Data Engineer** runs ``--fetch``:
    Pulls raw documents from external sources (SEC EDGAR, Newsweb, etc.)
    and saves them into the hierarchical structure under ``data/raw/{slug}/``.

    data/raw/soc_us/
      sec_filings/10-K/2026-02-27.txt
      sec_filings/10-Q/2025-11-13.txt
      sec_filings/8-K/2025-09-29.txt
      news/2025-09-01_q3_results.txt
      market_data/latest.json

    These files are auditable, human-readable, and committed to git.

**AI Engineer** runs ``--ingest``:
    Reads raw files from ``data/raw/{slug}/``, parses into Documents,
    chunks, embeds, and stores in SQLite + Qdrant under
    ``data/processed/{slug}/``.  No network required.

The research pipeline (Phases 1–3) only reads from ``data/processed/``.
If processed data doesn't exist yet, it auto-ingests from ``data/raw/``.
"""

from __future__ import annotations

import logging

from src.config import settings
from src.models.companies import CompanyConfig, expected_materials
from src.models.documents import CorpusSnapshot
from src.services.corpus_store import (
    CorpusStore,
    build_default_connectors,
)
from src.services.raw_store import (
    raw_company_dir,
    read_raw_documents,
    write_raw_documents,
)

logger = logging.getLogger(__name__)


# ── Step 1: Data Engineer — fetch and save raw files ─────────────────

async def fetch_corpus(
    profile: CompanyConfig,
    corpus_store: CorpusStore,
) -> tuple[list, str]:
    """Step 1 (Data Engineer): Fetch raw documents and save to data/raw/.

    Pulls from live connectors (requires internet).  Saves each document
    into the hierarchical folder structure:
        data/raw/{slug}/sec_filings/10-K/2026-02-27.txt
        data/raw/{slug}/news/2025-09-01_q3_results.txt
        data/raw/{slug}/market_data/latest.json

    Returns the list of documents fetched and a status string.
    """
    connectors = build_default_connectors(profile)
    all_documents = []

    for connector in connectors:
        logger.info(
            "Fetching source '%s' for %s (%s)",
            connector.source_key,
            profile.name,
            profile.ticker,
        )
        try:
            docs = await connector.fetch_documents(profile)
            all_documents.extend(docs)
            logger.info(
                "Fetched %d document(s) from '%s'",
                len(docs),
                connector.source_key,
            )
        except Exception:
            logger.exception(
                "Connector '%s' failed for %s (%s)",
                connector.source_key,
                profile.name,
                profile.ticker,
            )
        finally:
            # Ensure aiohttp sessions are always closed to avoid
            # "Unclosed client session/connector" warnings.
            try:
                await connector.disconnect()
            except Exception:
                logger.debug("Connector '%s' disconnect failed", connector.source_key, exc_info=True)

    if not all_documents:
        logger.warning("No documents fetched for %s (%s)", profile.name, profile.ticker)
        return [], "fetch_failed"

    # Write to hierarchical structure under data/raw/
    paths = write_raw_documents(profile, all_documents)
    logger.info(
        "Saved %d raw file(s) under data/raw/%s",
        len(paths),
        profile.slug,
    )

    return all_documents, f"fetched_{len(paths)}_files"


# ── Step 2: AI Engineer — build corpus from raw files ────────────────

async def ingest_corpus(
    profile: CompanyConfig,
    corpus_store: CorpusStore,
) -> tuple[CorpusSnapshot, str]:
    """Step 2 (AI Engineer): Read raw files → chunk → embed → store.

    Reads from ``data/raw/{slug}/``, builds SQLite + Qdrant under
    ``data/processed/{slug}/``.  No network access required.

    Falls back to legacy ``input_files/{slug}/`` if data/raw/ doesn't exist.
    """
    raw_dir = raw_company_dir(profile)
    legacy_dir = settings.input_files_dir / profile.slug

    # Determine where to read from
    if raw_dir.is_dir() and (any(raw_dir.rglob("*.txt")) or any(raw_dir.rglob("*.json"))):
        documents = read_raw_documents(profile)
        source = "data/raw"
    elif legacy_dir.is_dir() and any(legacy_dir.glob("*.txt")):
        # Legacy fallback: read old flat input_files/
        from src.connectors.local_files import LocalFilesConnector
        connector = LocalFilesConnector(input_dir=settings.input_files_dir)
        documents = await connector.fetch_documents(profile)
        source = "input_files"
    else:
        raise FileNotFoundError(
            f"No raw data found for {profile.ticker}.\n"
            f"  Expected: {raw_dir}\n"
            f"  Run step 1 first: python -m src.main --fetch \"{profile.ticker}\""
        )

    if not documents:
        raise ValueError(f"No documents loaded from {source}. Check that files exist and are non-empty.")

    # Store all documents and build chunks + embeddings
    corpus_store._replace_all_documents(profile, documents)
    chunks = corpus_store._rebuild_all_chunks(profile, documents)

    from src.models.documents import CorpusManifest, SourceStatus, utc_now_iso
    from collections import Counter

    source_counts = Counter(d.source_key for d in documents)
    now = utc_now_iso()
    statuses = [
        SourceStatus(
            connector=src_key,
            last_fetched=now,
            doc_count=count,
            status="fresh",
            error_message=None,
            refresh_reason=f"ingested_from_{source}",
        )
        for src_key, count in sorted(source_counts.items())
    ]
    manifest = CorpusManifest(
        ticker=profile.ticker,
        slug=profile.slug,
        created_at=now,
        last_refreshed_at=now,
        sources=statuses,
        total_documents=len(documents),
        total_chunks=len(chunks),
        metadata={"company_name": profile.name, "source": source},
    )
    corpus_store.save_manifest(profile, manifest)
    snapshot = CorpusSnapshot(manifest=manifest, documents=documents, chunks=chunks)

    return snapshot, "ingested"


# ── Pipeline entry point — load only ─────────────────────────────────

def _is_processed_stale(profile: CompanyConfig, corpus_store: CorpusStore) -> bool:
    """Check if raw data is newer than the processed corpus.

    Compares the most recent modification time of any file in data/raw/{slug}/
    against the processed manifest's last_refreshed_at timestamp.
    """
    raw_dir = raw_company_dir(profile)
    if not raw_dir.is_dir():
        return False  # No raw data — processed is fine as-is

    manifest = corpus_store.load_manifest(profile)
    if manifest is None:
        return True  # No manifest — definitely stale

    from datetime import datetime, timezone

    # Parse manifest timestamp
    try:
        processed_time = datetime.fromisoformat(
            manifest.last_refreshed_at.replace("Z", "+00:00")
        )
    except (ValueError, AttributeError):
        return True  # Can't parse — assume stale

    # Find newest raw file
    newest_raw = 0.0
    for f in raw_dir.rglob("*"):
        if f.is_file():
            newest_raw = max(newest_raw, f.stat().st_mtime)

    if newest_raw == 0.0:
        return False  # No raw files

    raw_time = datetime.fromtimestamp(newest_raw, tz=timezone.utc)
    return raw_time > processed_time


async def ensure_corpus_ready(
    profile: CompanyConfig,
    corpus_store: CorpusStore,
) -> tuple[CorpusSnapshot, str]:
    """Pipeline entry point: load what's on disk.  Never fetches live data.

    Priority:
    1. data/processed/{slug}/ exists (AI engineer ran --ingest) → load it
    2. data/raw/{slug}/ exists → auto-ingest from raw files
    3. input_files/{slug}/ exists (legacy) → auto-ingest from legacy files
    4. Neither exists → error with instructions
    """
    # 1. Processed corpus exists — check if it's stale vs raw data
    if corpus_store.corpus_exists(profile):
        if not _is_processed_stale(profile, corpus_store):
            return corpus_store.load_corpus(profile), "loaded"
        # Processed exists but raw is newer — re-ingest
        snapshot, status = await ingest_corpus(profile, corpus_store)
        return snapshot, f"re_{status}"

    # 2. Raw data exists — auto-ingest
    raw_dir = raw_company_dir(profile)
    has_raw = raw_dir.is_dir() and (any(raw_dir.rglob("*.txt")) or any(raw_dir.rglob("*.json")))

    # 3. Legacy input_files exists
    legacy_dir = settings.input_files_dir / profile.slug
    has_legacy = legacy_dir.is_dir() and any(legacy_dir.glob("*.txt"))

    if has_raw or has_legacy:
        snapshot, status = await ingest_corpus(profile, corpus_store)
        return snapshot, f"auto_{status}"

    # 4. Nothing available
    raise FileNotFoundError(
        _missing_corpus_message(profile.ticker, profile.slug)
    )


def _missing_corpus_message(ticker: str, slug: str) -> str:
    """User-facing explanation: research phases ≠ data loading."""
    return (
        f"### No ingested corpus for {ticker}\n\n"
        "Phases **1–3** (gap report, adversarial dossier, analyst brief) only read what is "
        f"already stored under `data/processed/{slug}/`. They **do not** download filings or news.\n\n"
        "**Fetching and ingesting** are separate **data-prep** steps—not part of typing a ticker in the UI:\n"
        f"- `python -m src.main --fetch \"{ticker}\"` — pull raw files into `data/raw/{slug}/`\n"
        f"- `python -m src.main --ingest \"{ticker}\"` — build SQLite + vectors into `data/processed/{slug}/`\n\n"
        "**Docker:** if you ran fetch/ingest on the host, the container must see the same tree "
        "(Compose mounts `./src/data`). An empty mount here will look like “no corpus” until "
        "those directories exist.\n"
    )


__all__ = [
    "ensure_corpus_ready",
    "expected_materials",
    "fetch_corpus",
    "ingest_corpus",
]
