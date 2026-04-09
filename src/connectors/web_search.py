"""
Web Search Connector
=====================

Runs DuckDuckGo text searches to find recent news articles and web
pages about a company. Results are converted to Document objects with
publication dates when available.

Key class:
- WebSearchConnector -- searches DuckDuckGo and returns Documents
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from src.config import settings
from src.connectors.base import BaseResearchConnector
from src.models.companies import CompanyConfig
from src.models.documents import Document

# Patterns to extract dates from titles or URLs (e.g. "Q3 2025", "November 2024")
_YEAR_RE = re.compile(r"\b(20[12]\d)\b")
_QUARTER_RE = re.compile(r"\b[Qq]([1-4])\s*(20[12]\d)\b")
_MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(20[12]\d)\b",
    re.IGNORECASE,
)
_MONTH_NUMBERS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}
_QUARTER_MONTH = {"1": "03", "2": "06", "3": "09", "4": "12"}


def _extract_date_from_text(title: str, url: str) -> str | None:
    """Best-effort date extraction from title or URL text."""
    combined = f"{title} {url}"
    # Try "Q3 2025" style
    qm = _QUARTER_RE.search(combined)
    if qm:
        return f"{qm.group(2)}-{_QUARTER_MONTH[qm.group(1)]}-01"
    # Try "November 2024" style
    mm = _MONTH_YEAR_RE.search(combined)
    if mm:
        month_num = _MONTH_NUMBERS[mm.group(1).lower()]
        return f"{mm.group(2)}-{month_num}-01"
    # Try bare year as last resort
    ym = _YEAR_RE.search(title)
    if ym:
        return f"{ym.group(1)}-01-01"
    return None


class WebSearchConnector(BaseResearchConnector):
    source_key = "web_search"
    source_name = "DuckDuckGo Search"
    refresh_hours = 24

    def __init__(self) -> None:
        super().__init__("https://duckduckgo.com")

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except Exception:
                return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        query = profile.web_queries[0] if profile.web_queries else profile.name
        results = DDGS().text(query, max_results=settings.news_result_limit)
        documents: list[Document] = []
        for item in results:
            date_text = item.get("date")
            if date_text and len(date_text) >= 10:
                try:
                    if datetime.fromisoformat(date_text[:10]) < cutoff.replace(tzinfo=None):
                        continue
                except ValueError:
                    pass

            # Use the API date if available, otherwise extract from title/URL
            published_at = date_text[:10] if date_text else None
            if not published_at:
                title_text = item.get("title") or ""
                url_text = item.get("href") or item.get("url") or ""
                published_at = _extract_date_from_text(title_text, url_text)

            documents.append(
                Document.create(
                    ticker=profile.ticker,
                    source_key=self.source_key,
                    source_name=self.source_name,
                    source_url=item.get("href") or item.get("url") or "",
                    doc_type="news",
                    title=item.get("title") or query,
                    raw_text=item.get("body") or item.get("snippet") or item.get("title") or "",
                    published_at=published_at,
                    metadata={"query": query},
                )
            )
        return documents[: settings.news_result_limit]
