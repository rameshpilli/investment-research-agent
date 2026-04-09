"""
SEC EDGAR Connector
====================

Retrieves recent SEC filings (10-K, 10-Q, 8-K, etc.) for a company
from the EDGAR XBRL submissions API. Parses the filing index to
extract document text and metadata.

Key class:
- EdgarConnector -- fetches SEC filings via the EDGAR JSON API
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.connectors.base import BaseResearchConnector, default_connector_headers
from src.models.companies import CompanyConfig
from src.models.documents import Document


# Maps month-end to fiscal quarter.
_QUARTER_MAP = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}


def _fiscal_label(form: str, primary_doc: str, filing_date: str) -> str:
    """Build a human-readable title like '10-K Annual Report FY2025 (filed 2026-02-27)'.

    Extracts the period-end date from the primary document filename
    (e.g. 'socc-20251231.htm' → 2025-12-31) and derives fiscal year/quarter.
    """
    # Try to extract period date from filename (e.g. socc-20251231.htm)
    match = re.search(r"(\d{4})(\d{2})(\d{2})", primary_doc)
    period_year, period_month, period_label = None, None, None
    if match:
        period_year = int(match.group(1))
        period_month = int(match.group(2))
        period_day = match.group(3)
        period_label = f"{match.group(1)}-{match.group(2)}-{period_day}"

    if form == "10-K":
        fy = f"FY{period_year}" if period_year else ""
        return f"10-K Annual Report {fy} (filed {filing_date})".strip()
    if form == "10-Q":
        quarter = _QUARTER_MAP.get(period_month, "") if period_month else ""
        fy = str(period_year) if period_year else ""
        q_label = f"{quarter} {fy}".strip() if quarter else fy
        return f"10-Q Quarterly Report {q_label} (filed {filing_date})".strip()
    # 8-K: include the period date if available
    if period_label:
        return f"8-K Current Report {period_label} (filed {filing_date})"
    return f"8-K Current Report (filed {filing_date})"


class EdgarConnector(BaseResearchConnector):
    source_key = "edgar"
    source_name = "SEC EDGAR"
    refresh_hours = None

    def __init__(self) -> None:
        super().__init__("https://data.sec.gov", headers=default_connector_headers())

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        if not profile.sec_registered:
            return []

        cik = profile.edgar_cik
        if not cik:
            return []

        submissions = await self.request(f"/submissions/CIK{cik}.json")
        recent = submissions.get("filings", {}).get("recent", {})
        entries = self.parse_recent_filings(recent)
        cutoff_year = datetime.now(timezone.utc).year - settings.years_of_filings

        documents: list[Document] = []
        for entry in entries:
            filing_date = entry.get("filingDate", "")
            if not filing_date or int(filing_date[:4]) < cutoff_year:
                continue
            if entry.get("form") not in {"10-K", "10-Q", "8-K"}:
                continue
            if len(documents) >= settings.max_docs_per_source:
                break
            accession = entry.get("accessionNumber", "").replace("-", "")
            primary_doc = entry.get("primaryDocument", "")
            if not accession or not primary_doc:
                continue
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession}/{primary_doc}"
            )
            raw_html = await self.fetch_text(filing_url)
            raw_text = self.html_to_text(raw_html)

            # Extract period-end date from the primary doc filename
            period_match = re.search(r"(\d{4})(\d{2})(\d{2})", primary_doc)
            period_end = None
            fiscal_quarter = None
            fiscal_year = None
            if period_match:
                fiscal_year = int(period_match.group(1))
                period_month = int(period_match.group(2))
                period_end = f"{period_match.group(1)}-{period_match.group(2)}-{period_match.group(3)}"
                fiscal_quarter = _QUARTER_MAP.get(period_month)

            title = _fiscal_label(entry["form"], primary_doc, filing_date)

            documents.append(
                self.build_text_document(
                    profile=profile,
                    source_key=self.source_key,
                    source_name=self.source_name,
                    source_url=filing_url,
                    doc_type=entry["form"],
                    title=title,
                    raw_text=raw_text,
                    filing_date=filing_date,
                    metadata={
                        "accession_number": entry.get("accessionNumber"),
                        "act": entry.get("act"),
                        "file_number": entry.get("fileNumber"),
                        "period_end_date": period_end,
                        "fiscal_quarter": fiscal_quarter,
                        "fiscal_year": fiscal_year,
                    },
                )
            )
        return documents

    @staticmethod
    def parse_recent_filings(recent: dict[str, list[Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        length = len(recent.get("form", []))
        for index in range(length):
            rows.append(
                {
                    "form": recent.get("form", [""])[index],
                    "filingDate": recent.get("filingDate", [""])[index],
                    "accessionNumber": recent.get("accessionNumber", [""])[index],
                    "primaryDocument": recent.get("primaryDocument", [""])[index],
                    "primaryDocDescription": recent.get("primaryDocDescription", [""])[index],
                    "act": recent.get("act", [""])[index],
                    "fileNumber": recent.get("fileNumber", [""])[index],
                }
            )
        return rows
