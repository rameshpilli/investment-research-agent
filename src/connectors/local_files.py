"""
Local Files Connector
======================

Reads pre-fetched documents from the ``input_files/{slug}/`` directory.
Each file has a simple header block (lines starting with ``Key: value``)
followed by a separator line and the raw text body.

This connector is the offline fallback: when the code is deployed into
a corporate environment without internet access, users can still run
the research pipeline against the files that ship with the repository.

Key class:
- LocalFilesConnector -- reads documents from disk, no network needed
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config import settings
from src.connectors.base import BaseResearchConnector
from src.models.companies import CompanyConfig
from src.models.documents import Document

_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z ]+):\s*(.*)$")
_SEPARATOR = "=" * 70


class LocalFilesConnector(BaseResearchConnector):
    """Load documents from ``input_files/{slug}/`` on disk.

    File format expected::

        Document: <title>
        Type: <doc_type>
        Source: <source_name>
        Date: <filing_date or 'undated'>
        URL: <source_url>
        Doc ID: <original_doc_id>
        Text length: <N> chars
        ======================================================================
        <raw text body>
    """

    source_key = "local_files"
    source_name = "Local Files"
    refresh_hours = None  # never stale — files only change on git pull

    def __init__(self, input_dir: Path | None = None) -> None:
        self.input_dir = input_dir or settings.input_files_dir
        super().__init__("file://local")

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        company_dir = self.input_dir / profile.slug
        if not company_dir.is_dir():
            return []

        documents: list[Document] = []
        for filepath in sorted(company_dir.iterdir()):
            if not filepath.is_file() or not filepath.name.endswith(".txt"):
                continue
            doc = self._parse_file(profile, filepath)
            if doc is not None:
                documents.append(doc)

        # Sort: prioritize EDGAR filings (10-K, 10-Q, 8-K) over news
        type_priority = {"10-K": 0, "10-Q": 1, "8-K": 2, "annual_report": 3}
        documents.sort(key=lambda d: (type_priority.get(d.doc_type, 99), d.filing_date or ""))
        return documents

    def _parse_file(self, profile: CompanyConfig, filepath: Path) -> Document | None:
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
        doc_type = headers.get("type", self._infer_type_from_filename(filepath.name))
        source_name = headers.get("source", "Local Files")
        source_url = headers.get("url", f"file://{filepath}")
        date_str = headers.get("date")
        filing_date = date_str if date_str and date_str != "undated" else None

        # Enrich bare EDGAR titles (e.g. "10-K" → "10-K Annual Report FY2025")
        if doc_type in {"10-K", "10-Q", "8-K"} and title == doc_type:
            title = self._enrich_edgar_title(doc_type, source_url, filing_date, filepath.name)

        # Map source names to source_keys for consistency
        source_key = self._source_key_from_name(source_name)

        return Document.create(
            ticker=profile.ticker,
            source_key=source_key,
            source_name=source_name,
            source_url=source_url,
            doc_type=doc_type,
            title=title,
            raw_text=raw_text,
            filing_date=filing_date,
            metadata={"loaded_from": str(filepath.name), "original_doc_id": headers.get("doc id")},
        )

    @staticmethod
    def _infer_type_from_filename(name: str) -> str:
        lower = name.lower()
        if lower.startswith("10-k") or lower.startswith("10_k"):
            return "10-K"
        if lower.startswith("10-q") or lower.startswith("10_q"):
            return "10-Q"
        if lower.startswith("8-k") or lower.startswith("8_k"):
            return "8-K"
        if "annual_report" in lower:
            return "annual_report"
        if "news" in lower:
            return "news"
        if "price" in lower:
            return "price_data"
        return "other"

    @staticmethod
    def _enrich_edgar_title(doc_type: str, source_url: str, filing_date: str | None, filename: str) -> str:
        """Build a descriptive title for bare EDGAR filings."""
        from src.connectors.edgar import _fiscal_label

        # Extract primary doc name from URL or filename for period detection
        # URL example: https://www.sec.gov/.../socc-20251231.htm
        primary_doc = source_url.rsplit("/", 1)[-1] if "/" in source_url else filename
        date_str = filing_date or ""
        return _fiscal_label(doc_type, primary_doc, date_str)

    @staticmethod
    def _source_key_from_name(source_name: str) -> str:
        lower = source_name.lower()
        if "edgar" in lower:
            return "edgar"
        if "newsweb" in lower or "oslo" in lower:
            return "newsweb"
        if "company ir" in lower or "ir" == lower:
            return "company_ir"
        if "yahoo" in lower or "yfinance" in lower:
            return "yfinance"
        if "duckduckgo" in lower or "web search" in lower or "search" in lower:
            return "web_search"
        return "local_files"
