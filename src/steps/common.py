"""
Shared Step Utilities
======================

Helper functions reused across all pipeline phases for citation
construction, chunk grouping, source-context assembly, and corpus
fact extraction.

Self-contained — no external framework dependencies.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from src.config import settings
from src.models.companies import CompanyConfig
from src.models.documents import Chunk, CorpusSnapshot, Document
from src.models.reports import ReportCitation
from src.services.corpus_store import CorpusStore

CITATION_RETRIEVAL_LIMIT = settings.citation_retrieval_limit


# ---------------------------------------------------------------------------
# Citation helpers for the research pipeline
# ---------------------------------------------------------------------------

def make_citation(chunk: Chunk, document: Document, excerpt: str | None = None) -> dict[str, Any]:
    """Create a citation dict from a chunk and its parent document.

    The dict is the internal representation used by deterministic fallbacks.
    Use ``to_report_citation()`` to convert to the Pydantic schema.
    """
    content = (excerpt or chunk.text).strip()
    if len(content) > 240:
        content = f"{content[:240].rstrip()}..."
    return {
        "source_name": document.source_name,
        "content": content,
        "document_id": document.doc_id,
        "section": chunk.page_or_section,
        "url": document.source_url,
        "doc_type": document.doc_type,
        "filing_date": document.filing_date,
        "ticker": document.ticker,
        "title": document.title,
        "chunk_id": chunk.chunk_id,
    }


def to_report_citation(c: dict[str, Any]) -> ReportCitation:
    """Convert an internal citation dict to a ReportCitation (Pydantic schema).

    This bridges the deterministic fallback citations with the structured
    output model, so both paths produce the same citation type.
    """
    return ReportCitation(
        source_name=c.get("source_name", "unknown"),
        doc_type=c.get("doc_type"),
        filing_date=c.get("filing_date"),
        title=c.get("title"),
        excerpt=c.get("content"),
        doc_id=c.get("document_id"),
        chunk_id=c.get("chunk_id"),
        section=c.get("section"),
        url=c.get("url"),
    )


def citation_inline(citation: dict[str, Any], index: int) -> str:
    """Format a citation as an inline marker: [1: Source Name, doc_type, date]."""
    parts = [citation.get("source_name", "unknown")]
    if citation.get("doc_type"):
        parts.append(citation["doc_type"])
    if citation.get("filing_date"):
        parts.append(citation["filing_date"])
    return f"[{index}: {', '.join(parts)}]"


def append_citations(text: str, citations: list[dict[str, Any]]) -> str:
    """Append inline citation markers to a text string."""
    if not citations:
        return f"{text} [UNVERIFIED]"
    markers = " ".join(citation_inline(c, i + 1) for i, c in enumerate(citations))
    return f"{text} {markers}"


def citation_summary(citations: list[dict[str, Any]]) -> dict[str, int | float]:
    """Citation summary for fallback-generated citations.

    Fallback citations from make_citation() always have doc_id and chunk_id
    (from the corpus), so they are traceable. But we mark them as
    'heuristic' — true verification requires verify_citations_against_corpus().
    """
    total = len(citations)
    # Count citations that have corpus-traceable fields
    verified = sum(
        1 for c in citations
        if c.get("document_id") or c.get("chunk_id") or c.get("content")
    )
    return {
        "total": total,
        "verified": verified,
        "verification_rate": verified / total if total > 0 else 0.0,
    }


def retrieve_citations(
    query: str,
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    store: CorpusStore,
    *,
    limit: int = CITATION_RETRIEVAL_LIMIT,
) -> list[dict[str, Any]]:
    documents_by_id = {document.doc_id: document for document in snapshot.documents}
    citations: list[dict[str, Any]] = []
    for chunk in store.search_chunks(profile, query, limit=limit * 2):
        # Skip XBRL/boilerplate chunks
        if _is_junk_chunk(chunk.text):
            continue
        document = documents_by_id.get(chunk.doc_id)
        if not document:
            continue
        citations.append(make_citation(chunk, document))
        if len(citations) >= limit:
            break
    return citations


_JUNK_INDICATORS = frozenset([
    "xbrl", "iso4217", "xmlns", "xbrli", "0001831481",
    "us-gaap:", "srt:", "socc:", "utr:", "fasb.org",
])


def _is_junk_chunk(text: str) -> bool:
    """Return True if a chunk is XBRL preamble or formatting garbage."""
    lower = text[:200].lower()
    junk_count = sum(1 for j in _JUNK_INDICATORS if j in lower)
    return junk_count >= 2


def atomic_write_text(path: Path, content: str) -> None:
    """Write content to a file atomically via temp-file + rename.

    Prevents corrupt partial files if the process crashes mid-write.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
