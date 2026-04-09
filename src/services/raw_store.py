"""
Raw Data Store
===============

Manages the hierarchical raw data layout on disk:

    data/raw/{slug}/
      sec_filings/
        10-K/
          2026-02-27.txt
        10-Q/
          2025-11-13.txt
        8-K/
          2025-09-29.txt
      regulatory/
        2025-02-06_fourth_quarter.txt
      company_reports/
        annual_report_2024.txt
      news/
        2025-09-01_q3_results.txt
      market_data/
        latest.json

This is the **Data Engineer's** output.  Files here are:
  - plain text (or JSON for structured data)
  - human-readable and auditable
  - committed to git
  - the source of truth for the AI pipeline

The **AI Engineer** reads from here via ``--ingest`` and builds
the processed corpus (SQLite + Qdrant) under ``data/processed/``.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from src.config import settings
from src.models.companies import CompanyConfig
from src.models.documents import Document

_SEPARATOR = "=" * 70

# ── Source → folder mapping ──────────────────────────────────────────

# Maps (source_key, doc_type) → subfolder path.
# If doc_type has its own subfolder (e.g. sec_filings/10-K), use that.
# Otherwise fall back to the source-level folder.

_FOLDER_MAP: dict[str, str] = {
    "edgar": "sec_filings",
    "newsweb": "regulatory",
    "company_ir": "company_reports",
    "yfinance": "market_data",
    "web_search": "news",
    "local_files": "other",
}

# Doc types that get their own subfolder within the source folder
_TYPED_SUBFOLDERS = {"10-K", "10-Q", "8-K"}


def _folder_for_doc(doc: Document) -> str:
    """Return the relative folder path for a document.

    Examples:
        edgar + 10-K  → sec_filings/10-K
        edgar + 8-K   → sec_filings/8-K
        web_search     → news
        yfinance       → market_data
        company_ir     → company_reports
    """
    source_folder = _FOLDER_MAP.get(doc.source_key, "other")
    if doc.doc_type in _TYPED_SUBFOLDERS:
        return f"{source_folder}/{doc.doc_type}"
    return source_folder


def _filename_for_doc(doc: Document) -> str:
    """Build a clean filename for a document.

    sec_filings: just the date → 2026-02-27.txt
    news:        date + title  → 2025-09-01_q3_results.txt
    market_data: latest.json   (structured data)
    company_reports: title     → annual_report_2024.txt
    """
    date_str = doc.filing_date or doc.published_at or doc.as_of_date

    # Market data — each doc type gets its own file
    if doc.source_key == "yfinance":
        if doc.doc_type == "price_history":
            return "price_history.csv"
        if doc.doc_type == "holders":
            return "holders.csv"
        if doc.doc_type == "market_summary":
            return "summary.json"
        return f"{doc.doc_type}.txt"

    # SEC filings → just the date (one file per filing date)
    if doc.doc_type in _TYPED_SUBFOLDERS and date_str:
        return f"{date_str}.txt"

    # Everything else → date + title slug
    title_slug = re.sub(r"[^a-z0-9]+", "_", doc.title.lower()).strip("_")[:80]
    if date_str:
        return f"{date_str}_{title_slug}.txt"
    return f"{title_slug}.txt"


# ── Writing raw files ────────────────────────────────────────────────

def raw_company_dir(profile: CompanyConfig) -> Path:
    """Return the raw data directory for a company."""
    return settings.raw_data_dir / profile.slug


def write_raw_document(profile: CompanyConfig, doc: Document) -> Path:
    """Write a single document to the hierarchical raw structure.

    Behavior per file type:
    - SEC filings (date-named): skip if file already exists (same filing)
    - Market data CSV: merge — append new date rows to existing CSV
    - Market data summary/holders: overwrite (always want latest)
    - News: skip if file already exists (same article)
    - Company reports: skip if file already exists

    Returns the path to the written file.
    """
    folder = raw_company_dir(profile) / _folder_for_doc(doc)
    folder.mkdir(parents=True, exist_ok=True)

    filename = _filename_for_doc(doc)
    filepath = folder / filename

    # Price history CSV → merge new rows into existing file
    if doc.doc_type == "price_history" and filepath.exists():
        _merge_price_csv(filepath, doc.raw_text)
        return filepath

    # Summary/holders → always overwrite with latest
    if doc.doc_type in {"market_summary", "holders"}:
        pass  # fall through to write
    # Everything else → skip if already exists (idempotent)
    elif filepath.exists():
        return filepath

    if doc.metadata.get("format") == "csv":
        filepath.write_text(doc.raw_text, encoding="utf-8")
    elif doc.source_key == "yfinance" and doc.structured_payload:
        # Write summary as JSON
        payload = {
            "document": doc.title,
            "type": doc.doc_type,
            "source": doc.source_name,
            "date": doc.as_of_date or doc.filing_date or "unknown",
            "url": doc.source_url,
            "doc_id": doc.doc_id,
            "data": doc.structured_payload,
        }
        filepath.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    else:
        # Write text with metadata header
        header = "\n".join([
            f"Document: {doc.title}",
            f"Type: {doc.doc_type}",
            f"Source: {doc.source_name}",
            f"Date: {doc.filing_date or doc.published_at or doc.as_of_date or 'undated'}",
            f"URL: {doc.source_url}",
            f"Doc ID: {doc.doc_id}",
            f"Text length: {len(doc.raw_text):,} chars",
            _SEPARATOR,
        ])
        filepath.write_text(header + "\n\n" + doc.raw_text, encoding="utf-8")

    return filepath


def _merge_price_csv(existing_path: Path, new_csv_text: str) -> None:
    """Merge new price rows into an existing CSV, avoiding duplicates.

    Keeps all existing rows + appends any rows with dates not yet present.
    Result is sorted by date.
    """
    import csv as csv_mod

    # Read existing dates
    existing_text = existing_path.read_text(encoding="utf-8")
    existing_reader = csv_mod.DictReader(existing_text.strip().splitlines())
    existing_rows = list(existing_reader)
    existing_dates = {row.get("date") for row in existing_rows}

    # Read new rows
    new_reader = csv_mod.DictReader(new_csv_text.strip().splitlines())
    new_rows = list(new_reader)

    # Append only genuinely new dates
    added = 0
    for row in new_rows:
        if row.get("date") not in existing_dates:
            existing_rows.append(row)
            existing_dates.add(row["date"])
            added += 1

    if added == 0:
        return  # nothing new

    # Sort by date and rewrite
    existing_rows.sort(key=lambda r: r.get("date", ""))
    fieldnames = list(existing_rows[0].keys()) if existing_rows else []
    output = io.StringIO()
    writer = csv_mod.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(existing_rows)
    existing_path.write_text(output.getvalue(), encoding="utf-8")


def write_raw_documents(profile: CompanyConfig, docs: list[Document]) -> list[Path]:
    """Write all documents for a company to the raw structure."""
    return [write_raw_document(profile, doc) for doc in docs if doc.raw_text.strip() or doc.structured_payload]


# ── Reading raw files ────────────────────────────────────────────────

_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z ]+):\s*(.*)$")


def read_raw_documents(profile: CompanyConfig) -> list[Document]:
    """Read all raw documents for a company from the hierarchical structure.

    Walks the directory tree under ``data/raw/{slug}/`` and parses each
    ``.txt`` and ``.json`` file into a Document.
    """
    company_dir = raw_company_dir(profile)
    if not company_dir.is_dir():
        return []

    documents: list[Document] = []
    for filepath in sorted(company_dir.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.suffix == ".csv":
            doc = _parse_csv_file(profile, filepath)
        elif filepath.suffix == ".json":
            doc = _parse_json_file(profile, filepath)
        elif filepath.suffix == ".txt":
            doc = _parse_text_file(profile, filepath)
        else:
            continue
        if doc is not None:
            # Reclassify news articles that are actually regulatory announcements
            # (for non-SEC companies where Newsweb is the primary disclosure channel)
            if doc.doc_type == "news" and not profile.sec_registered:
                if _is_regulatory_title(doc.title):
                    doc = doc.model_copy(update={"doc_type": "regulatory_announcement"})
            documents.append(doc)

    return documents


def _parse_text_file(profile: CompanyConfig, filepath: Path) -> Document | None:
    """Parse a text file with metadata header into a Document."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    lines = content.split("\n")
    headers: dict[str, str] = {}
    body_start = 0

    for i, line in enumerate(lines):
        if line.strip().startswith("=" * 10):
            body_start = i + 1
            break
        match = _HEADER_RE.match(line)
        if match:
            headers[match.group(1).strip().lower()] = match.group(2).strip()

    raw_text = "\n".join(lines[body_start:]).strip()
    if not raw_text:
        return None

    title = headers.get("document", filepath.stem)
    doc_type = headers.get("type", _infer_doc_type(filepath))
    source_name = headers.get("source", "Unknown")
    source_url = headers.get("url", f"file://{filepath}")
    date_str = headers.get("date")
    filing_date = date_str if date_str and date_str != "undated" else None

    # Enrich bare EDGAR titles
    if doc_type in {"10-K", "10-Q", "8-K"} and title == doc_type:
        from src.connectors.edgar import _fiscal_label
        primary_doc = source_url.rsplit("/", 1)[-1] if "/" in source_url else filepath.name
        title = _fiscal_label(doc_type, primary_doc, filing_date or "")

    source_key = _source_key_from_path(filepath, source_name)

    return Document.create(
        ticker=profile.ticker,
        source_key=source_key,
        source_name=source_name,
        source_url=source_url,
        doc_type=doc_type,
        title=title,
        raw_text=raw_text,
        filing_date=filing_date,
        metadata={
            "raw_file": str(filepath.relative_to(settings.raw_data_dir)),
            "original_doc_id": headers.get("doc id"),
        },
    )


def _parse_csv_file(profile: CompanyConfig, filepath: Path) -> Document | None:
    """Parse a CSV file (price_history.csv) into a Document."""
    try:
        raw_text = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    if not raw_text.strip():
        return None

    return Document.create(
        ticker=profile.ticker,
        source_key="yfinance",
        source_name="Yahoo Finance",
        source_url=f"https://finance.yahoo.com/quote/{profile.yfinance_ticker}",
        doc_type="price_history",
        title=f"{profile.name} price history",
        raw_text=raw_text,
        metadata={
            "raw_file": str(filepath.relative_to(settings.raw_data_dir)),
            "format": "csv",
        },
    )


def _parse_json_file(profile: CompanyConfig, filepath: Path) -> Document | None:
    """Parse a JSON file (market_data) into a Document."""
    try:
        payload = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        # Might be a text file with .json extension (from migration)
        return _parse_text_file(profile, filepath)

    title = payload.get("document", filepath.stem)
    doc_type = payload.get("type", "price_data")
    source_name = payload.get("source", "Yahoo Finance")
    source_url = payload.get("url", f"file://{filepath}")
    date_str = payload.get("date")
    raw_text = payload.get("raw_text", "")
    structured = payload.get("data")

    # Build raw_text from structured if needed
    if not raw_text and structured:
        raw_text = json.dumps(structured, indent=2, default=str)

    return Document.create(
        ticker=profile.ticker,
        source_key="yfinance",
        source_name=source_name,
        source_url=source_url,
        doc_type=doc_type,
        title=title,
        raw_text=raw_text,
        as_of_date=date_str if date_str and date_str != "unknown" else None,
        structured_payload=structured,
        metadata={"raw_file": str(filepath.relative_to(settings.raw_data_dir))},
    )


def _infer_doc_type(filepath: Path) -> str:
    """Infer doc_type from the directory structure."""
    parts = filepath.relative_to(settings.raw_data_dir).parts
    # parts: (slug, folder, maybe_subfolder, filename)
    if len(parts) >= 3:
        folder = parts[1]        # sec_filings, news, etc.
        subfolder = parts[2] if len(parts) >= 4 else None
        if subfolder in {"10-K", "10-Q", "8-K"}:
            return subfolder
        if folder == "regulatory":
            return "regulatory_announcement"
        if folder == "company_reports":
            return "annual_report"
        if folder == "news":
            return "news"
        if folder == "market_data":
            return "price_data"
    return "other"


def _source_key_from_path(filepath: Path, source_name: str) -> str:
    """Determine source_key from the folder structure or source name."""
    try:
        parts = filepath.relative_to(settings.raw_data_dir).parts
        if len(parts) >= 2:
            folder = parts[1]
            folder_to_key = {
                "sec_filings": "edgar",
                "regulatory": "newsweb",
                "company_reports": "company_ir",
                "market_data": "yfinance",
                "news": "web_search",
            }
            if folder in folder_to_key:
                return folder_to_key[folder]
    except ValueError:
        pass

    # Fallback: infer from source_name header
    lower = source_name.lower()
    if "edgar" in lower:
        return "edgar"
    if "newsweb" in lower or "oslo" in lower:
        return "newsweb"
    if "company ir" in lower or "ir" == lower:
        return "company_ir"
    if "yahoo" in lower or "yfinance" in lower:
        return "yfinance"
    if "duckduckgo" in lower or "web search" in lower:
        return "web_search"
    return "local_files"


_REGULATORY_KEYWORDS = [
    "quarter", "results", "annual", "half year", "half-year",
    "interim", "report", "dividend", "agm", "general meeting",
    "remuneration", "financial calendar",
]


def _is_regulatory_title(title: str) -> bool:
    """Check if a news article title is actually a regulatory announcement."""
    lower = title.lower()
    return any(kw in lower for kw in _REGULATORY_KEYWORDS)


def list_raw_summary(profile: CompanyConfig) -> dict[str, Any]:
    """Return a summary of raw files for a company."""
    company_dir = raw_company_dir(profile)
    if not company_dir.is_dir():
        return {"exists": False, "path": str(company_dir)}

    summary: dict[str, int] = {}
    total = 0
    for filepath in company_dir.rglob("*"):
        if filepath.is_file() and filepath.suffix in {".txt", ".json", ".csv"}:
            # Get the relative folder path
            rel = filepath.relative_to(company_dir)
            folder = str(rel.parent) if rel.parent != Path(".") else "root"
            summary[folder] = summary.get(folder, 0) + 1
            total += 1

    return {"exists": True, "path": str(company_dir), "total_files": total, "folders": summary}
