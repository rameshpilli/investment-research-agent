"""
Company Investor Relations Connector
======================================

Scrapes a company's investor-relations web page for annual reports,
investor presentations, and similar filings. Downloads PDFs and HTML
pages, converts them to plain text, and returns Document objects.

Key class:
- CompanyIRConnector -- fetches and parses IR page links into Documents
"""

from __future__ import annotations

from src.config import settings
from src.connectors.base import BaseResearchConnector
from src.models.companies import CompanyConfig
from src.models.documents import Document


class CompanyIRConnector(BaseResearchConnector):
    source_key = "company_ir"
    source_name = "Company IR"
    refresh_hours = None

    def __init__(self) -> None:
        super().__init__("https://www.akersolutions.com")

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        if not profile.ir_url:
            return []

        content = await self.fetch_text(profile.ir_url)
        candidates = self.parse_report_links(content, profile.ir_url)
        documents: list[Document] = []
        for url, title in candidates[: settings.max_docs_per_source]:
            raw_text = ""
            lower_url = url.lower()
            # Skip non-document files (zip, xbrl, etc.)
            if any(lower_url.endswith(ext) for ext in (".zip", ".xbrl", ".json", ".xml")):
                continue
            doc_type = "investor_presentation" if "presentation" in title.lower() else "annual_report"
            try:
                if lower_url.endswith(".pdf"):
                    raw_text = await self._extract_pdf_text(url)
                elif "ixbrl" in lower_url:
                    continue  # skip interactive XBRL viewers
                else:
                    raw_html = await self.fetch_text(url)
                    raw_text = self.html_to_text(raw_html)
            except Exception:
                continue  # skip files that fail to download/parse
            documents.append(
                Document.create(
                    ticker=profile.ticker,
                    source_key=self.source_key,
                    source_name=self.source_name,
                    source_url=url,
                    doc_type=doc_type,
                    title=title,
                    raw_text=raw_text or title,
                    metadata={"ir_url": profile.ir_url},
                )
            )
        return documents

    @classmethod
    def parse_report_links(cls, content: str, base_url: str) -> list[tuple[str, str]]:
        links = cls.extract_links(content, base_url)
        candidates: list[tuple[str, str]] = []
        for href, text in links:
            lowered = f"{href} {text}".lower()
            if not any(keyword in lowered for keyword in ("report", "results", "presentation", "quarter", "annual")):
                continue
            candidates.append((href, text or href.rsplit("/", 1)[-1]))
        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        for href, text in candidates:
            if href in seen:
                continue
            deduped.append((href, text))
            seen.add(href)
        return deduped

    async def _extract_pdf_text(self, url: str) -> str:
        data = await self.fetch_bytes(url)
        from src.services.pdf_parser import parse_pdf_bytes
        return parse_pdf_bytes(data)
