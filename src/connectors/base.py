"""
Base Research Connector
========================

Abstract base class for all external data connectors. Provides shared
HTTP fetching, HTML link parsing, PDF text extraction, and a uniform
``fetch_documents`` interface that each concrete connector implements.

Self-contained — no external framework dependencies.

Key classes:
- ConnectorConfig       -- simple configuration dataclass
- BaseResearchConnector -- ABC with helpers for HTTP requests, HTML
  parsing, and PDF extraction
- _LinkParser           -- internal HTML parser that extracts anchor tags
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from src.config import settings
from src.models.companies import CompanyConfig
from src.models.documents import Document


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ConnectorConfig:
    """Configuration for a connector instance."""

    name: str
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HTML link parser (internal helper)
# ---------------------------------------------------------------------------

class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_map = dict(attrs)
        self._href = attrs_map.get("href")
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        text = " ".join(part.strip() for part in self._parts if part.strip())
        self.links.append((self._href, text))
        self._href = None
        self._parts = []


# ---------------------------------------------------------------------------
# Abstract base connector
# ---------------------------------------------------------------------------

class BaseResearchConnector(ABC):
    """Abstract base for research data connectors.

    Provides HTTP helpers, HTML parsing, PDF text extraction, and a
    uniform ``fetch_documents`` interface.
    """

    source_key: str = ""
    source_name: str = ""
    refresh_hours: int | None = None

    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self.config = ConnectorConfig(
            name=self.source_key,
            base_url=base_url,
            headers=headers or {},
        )
        self._session = None

    async def connect(self) -> None:
        import aiohttp

        self._session = aiohttp.ClientSession(headers=self.config.headers, raise_for_status=True)

    async def disconnect(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def request(self, endpoint: str, method: str = "GET", **kwargs) -> dict[str, Any]:
        if self._session is None:
            await self.connect()
        url = endpoint if endpoint.startswith("http") else f"{self.config.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        async with self._session.request(method, url, **kwargs) as response:
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                return await response.json()
            return {"text": await response.text()}

    async def fetch_text(self, url: str) -> str:
        payload = await self.request(url)
        if "text" in payload:
            return str(payload["text"])
        return json.dumps(payload, default=str)

    async def fetch_bytes(self, url: str) -> bytes:
        if self._session is None:
            await self.connect()
        async with self._session.get(url) as response:
            return await response.read()

    @abstractmethod
    async def fetch_documents(self, profile: CompanyConfig) -> list[Document]:
        raise NotImplementedError

    def is_stale(self, last_synced_at: str | None) -> bool:
        from datetime import datetime, timedelta, timezone

        if not last_synced_at or self.refresh_hours is None:
            return True
        parsed = datetime.fromisoformat(last_synced_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - parsed >= timedelta(hours=self.refresh_hours)

    @staticmethod
    def html_to_text(content: str) -> str:
        stripped = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", content)
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        return re.sub(r"\s+", " ", unescape(stripped)).strip()

    @staticmethod
    def extract_links(content: str, base_url: str) -> list[tuple[str, str]]:
        parser = _LinkParser()
        parser.feed(content)
        return [(urljoin(base_url, href), text) for href, text in parser.links if href]

    @staticmethod
    def build_text_document(
        *,
        profile: CompanyConfig,
        source_key: str,
        source_name: str,
        source_url: str,
        doc_type: str,
        title: str,
        raw_text: str,
        filing_date: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        return Document.create(
            ticker=profile.ticker,
            source_key=source_key,
            source_name=source_name,
            source_url=source_url,
            doc_type=doc_type,
            title=title,
            raw_text=raw_text,
            filing_date=filing_date,
            metadata=metadata,
        )


def default_connector_headers() -> dict[str, str]:
    return {"User-Agent": settings.sec_user_agent}
