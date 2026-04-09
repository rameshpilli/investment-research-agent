"""
Document and Corpus Models
============================

Pydantic models representing the document lifecycle: raw documents
fetched by connectors, text chunks produced during ingestion, and
corpus snapshots that tie everything together with versioning.

Key classes:
- Document        -- a single fetched document with metadata
- Chunk           -- a text fragment derived from a Document
- CorpusSnapshot  -- versioned bundle of documents and chunks
- CorpusManifest  -- lightweight metadata for a stored corpus

Key helpers:
- slugify_ticker  -- normalize a ticker into a filesystem-safe slug
- stable_hash     -- deterministic SHA-256 hash for deduplication
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

CORPUS_VERSION = "2026-04-02"
PARSER_VERSION = "1.1"
CHUNKING_VERSION = "1.1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify_ticker(ticker: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", ticker.lower()).strip("_")
    return cleaned or "unknown"


def stable_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Document(BaseModel):
    doc_id: str
    ticker: str
    source_key: str
    source_name: str
    source_url: str
    doc_type: str
    filing_date: str | None = None
    published_at: str | None = None
    as_of_date: str | None = None
    title: str
    raw_text: str = ""
    structured_payload: dict[str, Any] | list[Any] | None = None
    content_hash: str
    ingested_at: str = Field(default_factory=utc_now_iso)
    parser_version: str = PARSER_VERSION
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        ticker: str,
        source_key: str,
        source_name: str,
        source_url: str,
        doc_type: str,
        title: str,
        raw_text: str = "",
        structured_payload: dict[str, Any] | list[Any] | None = None,
        filing_date: str | None = None,
        published_at: str | None = None,
        as_of_date: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Document":
        metadata = metadata or {}
        content_blob = raw_text or json.dumps(structured_payload or {}, sort_keys=True, default=str)
        identity = "|".join([
            ticker,
            source_key,
            source_url,
            doc_type,
            filing_date or "",
            published_at or "",
            title,
        ])
        return cls(
            doc_id=stable_hash(identity),
            ticker=ticker,
            source_key=source_key,
            source_name=source_name,
            source_url=source_url,
            doc_type=doc_type,
            filing_date=filing_date,
            published_at=published_at,
            as_of_date=as_of_date,
            title=title,
            raw_text=raw_text,
            structured_payload=structured_payload,
            content_hash=stable_hash(content_blob),
            metadata=metadata,
        )


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    ticker: str
    chunk_index: int
    text: str
    page_or_section: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    chunking_version: str = CHUNKING_VERSION
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        doc: Document,
        chunk_index: int,
        text: str,
        page_or_section: str | None = None,
        char_start: int | None = None,
        char_end: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Chunk":
        metadata = metadata or {}
        return cls(
            chunk_id=stable_hash(f"{doc.doc_id}|{chunk_index}"),
            doc_id=doc.doc_id,
            ticker=doc.ticker,
            chunk_index=chunk_index,
            text=text,
            page_or_section=page_or_section,
            char_start=char_start,
            char_end=char_end,
            metadata=metadata,
        )


class SourceStatus(BaseModel):
    connector: str
    last_fetched: str
    doc_count: int
    status: str
    error_message: str | None = None
    refresh_reason: str | None = None


class CorpusManifest(BaseModel):
    ticker: str
    slug: str
    corpus_version: str = CORPUS_VERSION
    created_at: str = Field(default_factory=utc_now_iso)
    last_refreshed_at: str = Field(default_factory=utc_now_iso)
    parser_version: str = PARSER_VERSION
    chunking_version: str = CHUNKING_VERSION
    sources: list[SourceStatus] = Field(default_factory=list)
    total_documents: int = 0
    total_chunks: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def source_map(self) -> dict[str, SourceStatus]:
        return {status.connector: status for status in self.sources}


class CorpusSnapshot(BaseModel):
    manifest: CorpusManifest
    documents: list[Document]
    chunks: list[Chunk]
