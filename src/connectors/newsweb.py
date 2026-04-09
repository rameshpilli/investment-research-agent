"""
Oslo Bors Newsweb Connector
=============================

Fetches regulatory announcements for Oslo-listed companies.

Primary path: Oslo Bors Newsweb (newsweb.oslobors.no)
Fallback:     DuckDuckGo web search filtered for regulatory content

Note: Newsweb is a JavaScript SPA that returns an empty shell to
plain HTTP requests. The connector detects this and falls back to
web search, filtering results for regulatory-style announcements
(quarterly results, annual results, regulatory filings).

Key class:
- NewswebConnector -- fetches regulatory announcements
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from xml.etree import ElementTree

from src.config import settings
from src.connectors.base import BaseResearchConnector
from src.models.companies import CompanyConfig
from src.models.documents import Document

# Keywords that indicate a news result is actually a regulatory announcement
_REGULATORY_KEYWORDS = [
    "quarter", "results", "annual", "half year", "half-year",
    "interim", "report", "dividend", "agm", "general meeting",
    "remuneration", "share buyback", "mandatory notification",
    "financial calendar", "prospectus",
]


class NewswebConnector(BaseResearchConnector):
    source_key = "newsweb"
    source_name = "Oslo Bors Newsweb"
    refresh_hours = None

    def __init__(self) -> None:
        super().__init__("https://newsweb.oslobors.no")

    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        if not profile.newsweb_query:
            return []

        # Try Newsweb first
        if profile.newsweb_url:
            docs = await self._try_newsweb(profile)
            if docs:
                return docs

        # Fallback: use DuckDuckGo to find regulatory announcements
        return await self._fallback_web_search(profile)

    async def _try_newsweb(self, profile: CompanyConfig) -> list[Document]:
        """Try the Newsweb HTML/RSS endpoint. Returns [] if it's a JS SPA shell."""
        try:
            content = await self.fetch_text(profile.newsweb_url)
        except Exception:
            return []

        # Newsweb is a JS SPA — if we get a shell back, there's no data
        if len(content) < 1000 and "noscript" in content.lower():
            return []

        entries = self.parse_listing(content, profile.newsweb_url)
        cutoff_year = datetime.now(timezone.utc).year - settings.years_of_filings

        documents: list[Document] = []
        for entry in entries[: settings.max_docs_per_source]:
            published_at = entry.get("published_at")
            if published_at and int(published_at[:4]) < cutoff_year:
                continue
            body = entry.get("summary", "") or entry.get("title", "")
            documents.append(
                Document.create(
                    ticker=profile.ticker,
                    source_key=self.source_key,
                    source_name=self.source_name,
                    source_url=entry["url"],
                    doc_type="regulatory_announcement",
                    title=entry["title"],
                    raw_text=body,
                    filing_date=published_at,
                    published_at=published_at,
                    metadata={"listing_source": profile.newsweb_url},
                )
            )
        return documents

    async def _fallback_web_search(self, profile: CompanyConfig) -> list[Document]:
        """Fallback: use DuckDuckGo to find regulatory-style announcements."""
        try:
            from ddgs import DDGS
        except ImportError:
            try:
                from duckduckgo_search import DDGS
            except Exception:
                return []

        queries = [
            f"{profile.name} quarterly results",
            f"{profile.name} annual results regulatory announcement",
        ]

        seen_urls: set[str] = set()
        documents: list[Document] = []

        for query in queries:
            try:
                results = DDGS().text(query, max_results=15)
            except Exception:
                continue

            for item in results:
                url = item.get("href") or item.get("url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = item.get("title") or ""
                # Only keep results that look like regulatory announcements
                if not _is_regulatory(title):
                    continue

                body = item.get("body") or item.get("snippet") or title
                date_text = item.get("date")
                published_at = date_text[:10] if date_text else None

                documents.append(
                    Document.create(
                        ticker=profile.ticker,
                        source_key=self.source_key,
                        source_name="Oslo Bors Newsweb (via web search)",
                        source_url=url,
                        doc_type="regulatory_announcement",
                        title=title,
                        raw_text=body,
                        published_at=published_at,
                        metadata={"query": query, "fallback": True},
                    )
                )

        return documents[: settings.max_docs_per_source]

    @classmethod
    def parse_listing(cls, content: str, base_url: str) -> list[dict[str, Any]]:
        content = content.strip()
        if content.startswith("<?xml") or "<rss" in content[:200]:
            return cls._parse_rss(content)
        links = cls.extract_links(content, base_url)
        rows: list[dict[str, Any]] = []
        for href, text in links:
            if not text:
                continue
            lowered = text.lower()
            if not _is_regulatory(lowered):
                continue
            date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
            rows.append(
                {
                    "title": text,
                    "url": href,
                    "published_at": date_match.group(1) if date_match else None,
                    "summary": unescape(text),
                }
            )
        return rows

    @staticmethod
    def _parse_rss(content: str) -> list[dict[str, Any]]:
        root = ElementTree.fromstring(content)
        rows: list[dict[str, Any]] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            description = (item.findtext("description") or "").strip()
            rows.append(
                {
                    "title": title,
                    "url": link,
                    "published_at": pub_date[:10] if pub_date else None,
                    "summary": BaseResearchConnector.html_to_text(description),
                }
            )
        return rows


def _is_regulatory(text: str) -> bool:
    """Check if text looks like a regulatory announcement title."""
    lower = text.lower()
    return any(kw in lower for kw in _REGULATORY_KEYWORDS)
