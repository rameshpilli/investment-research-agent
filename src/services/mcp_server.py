"""
Investment Research MCP Tool Server
====================================

Exposes the stored company corpus as tools that Claude agents can call
to search, explore, and analyze ingested documents.

Architecture:
    The tool server sits between the pre-processing layer (Layer 1) and the AI
    research layer (Layer 2). Layer 1 builds the corpus (SQLite + Qdrant). The
    tools provide read-only access to that corpus so Claude agents can search,
    compare filings, and identify gaps without direct database access.

Tools provided:
    - list_available_companies: List all supported companies
    - search_corpus:          Semantic search over stored chunks
    - list_documents:         List all documents in the corpus
    - get_document:           Get full text for a specific document
    - get_filing_section:     Extract a specific section/topic from a filing
    - compare_filings:        Side-by-side comparison for tone shift detection
    - get_missing_materials:  What expected documents are absent
    - get_corpus_status:      Corpus metadata and freshness

Usage:
    from src.services.mcp_server import ResearchToolServer, TOOLS_ANTHROPIC

    server = ResearchToolServer(profile, corpus_store)
    result = server.call("search_corpus", {"query": "revenue growth"})

See Also:
    - src.services.agent: Claude agent loop that calls these tools
    - src.services.corpus_store: Storage layer these tools read from
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from src.config import settings
from src.models.companies import CompanyConfig, expected_materials_rich
from src.models.documents import CorpusSnapshot
from src.services.corpus_store import CorpusStore

DEFAULT_SEARCH_LIMIT = settings.mcp_search_default_limit
DOCUMENT_CHAR_LIMIT = settings.mcp_document_char_limit
SECTION_FALLBACK_CHUNKS = settings.mcp_section_fallback_chunks
SECTION_MATCH_LIMIT = settings.mcp_section_match_limit

# ---------------------------------------------------------------------------
# Shared tool catalog
# ---------------------------------------------------------------------------

TOOLS_ANTHROPIC: list[dict[str, Any]] = [
    {
        "name": "list_available_companies",
        "description": (
            "List all companies available for research. Returns each company's ticker, name, "
            "exchange, country, and whether a corpus has been prepared. "
            "Call this FIRST before any other tool — the user may not know the exact ticker format."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_corpus",
        "description": (
            "Semantic search over stored document chunks for a company. "
            "Returns ranked results with text, source name, doc type, filing date, and citation info. "
            "Use this to find evidence for a specific claim or topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query — describe what you're looking for"},
                "limit": {"type": "integer", "description": "Max results to return", "default": DEFAULT_SEARCH_LIMIT},
                "doc_type": {"type": "string", "description": "Filter by doc type (e.g. '10-K', '8-K', 'news'). Omit for all."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_documents",
        "description": (
            "List all documents in the corpus for this company. "
            "Returns title, doc_type, source, filing_date, and doc_id for each. "
            "Use this first to understand what's available before diving into specifics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_filter": {"type": "string", "description": "Filter by source_key (e.g. 'edgar', 'newsweb'). Omit for all."},
            },
            "required": [],
        },
    },
    {
        "name": "get_document",
        "description": "Get full text and metadata for a specific document by doc_id.",
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string", "description": "The document ID"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "get_filing_section",
        "description": (
            "Search within a specific document for a section or topic. "
            "Returns chunks from that document matching the keyword. "
            "Use for extracting risk factors, MD&A, or other specific sections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "The document ID to search within"},
                "section_keyword": {"type": "string", "description": "Topic or section to find (e.g. 'risk factors', 'revenue')"},
            },
            "required": ["doc_id", "section_keyword"],
        },
    },
    {
        "name": "compare_filings",
        "description": (
            "Compare how two documents discuss the same topic. "
            "Returns side-by-side text from both documents on the given topic. "
            "Use this for tone shift detection — compare management language across periods."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id_1": {"type": "string", "description": "First document ID (e.g. older filing)"},
                "doc_id_2": {"type": "string", "description": "Second document ID (e.g. newer filing)"},
                "topic": {"type": "string", "description": "Topic to compare (e.g. 'guidance', 'risk', 'revenue')"},
            },
            "required": ["doc_id_1", "doc_id_2", "topic"],
        },
    },
    {
        "name": "get_missing_materials",
        "description": "What expected document types are missing from the corpus? Returns each gap with criticality and why it matters.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_corpus_status",
        "description": "Get corpus metadata: when built, last refreshed, source freshness, document and chunk counts.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

class ResearchToolServer:
    """Executes tool calls against a CorpusStore for a specific company.

    Each method corresponds to one of the tools defined above. The Claude agent
    sends a tool_use block with name + input, and this class dispatches to the
    right handler and returns a JSON string result.
    """

    def __init__(self, profile: CompanyConfig, store: CorpusStore) -> None:
        self.profile = profile
        self.store = store
        self._snapshot: CorpusSnapshot | None = None

    @property
    def snapshot(self) -> CorpusSnapshot:
        if self._snapshot is None:
            self._snapshot = self.store.load_corpus(self.profile)
        return self._snapshot

    def invalidate_cache(self) -> None:
        self._snapshot = None

    def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a JSON string."""
        if tool_name == "list_available_companies":
            return json.dumps(self._tool_list_available_companies(), default=str, ensure_ascii=False)
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            result = handler(**arguments)
            return json.dumps(result, default=str, ensure_ascii=False)
        except Exception as exc:
            logger.error("Tool %s failed with args %s: %s", tool_name, arguments, exc, exc_info=True)
            return json.dumps({"error": str(exc)})

    def _document_fields(self, doc_id: str) -> dict[str, Any]:
        document = next((doc for doc in self.snapshot.documents if doc.doc_id == doc_id), None)
        if document is None:
            return {
                "doc_type": "unknown",
                "source_name": "unknown",
                "title": "unknown",
                "filing_date": None,
                "source_url": None,
            }
        return {
            "doc_type": document.doc_type,
            "source_name": document.source_name,
            "title": document.title,
            "filing_date": document.filing_date,
            "source_url": document.source_url,
        }

    # -- tool implementations --------------------------------------------------

    def _tool_list_available_companies(self) -> list[dict]:
        from src.models.companies import COMPANIES
        results = []
        for config in sorted(COMPANIES.values(), key=lambda item: item.ticker):
            corpus_ready = self.store.corpus_exists(config)
            results.append({
                "ticker": config.ticker,
                "name": config.name,
                "exchange": config.exchange,
                "country": config.country,
                "currency": config.currency,
                "sec_registered": config.sec_registered,
                "corpus_ready": corpus_ready,
                "enabled_connectors": config.enabled_connectors,
            })
        return results

    def _tool_search_corpus(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT, doc_type: str | None = None) -> list[dict]:
        filters = {"doc_type": doc_type} if doc_type else None
        chunks = self.store.search_chunks(self.profile, query, limit=limit, filters=filters)
        results: list[dict[str, Any]] = []
        for chunk in chunks:
            results.append(
                {
                    "text": chunk.text,
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "page_or_section": chunk.page_or_section,
                    **self._document_fields(chunk.doc_id),
                }
            )
        return results

    def _tool_list_documents(self, source_filter: str | None = None) -> list[dict]:
        docs = self.store.get_documents_for_source(self.profile, source_filter) if source_filter else self.snapshot.documents
        return [
            {"doc_id": d.doc_id, "title": d.title, "doc_type": d.doc_type,
             "source_name": d.source_name, "source_key": d.source_key,
             "filing_date": d.filing_date, "source_url": d.source_url}
            for d in docs
        ]

    def _tool_get_document(self, doc_id: str) -> dict:
        doc = self.store.get_document(self.profile, doc_id)
        if doc is None:
            return {"error": f"Document {doc_id} not found"}
        text = doc.raw_text
        if len(text) > DOCUMENT_CHAR_LIMIT:
            text = text[:DOCUMENT_CHAR_LIMIT] + f"\n\n[... truncated, {len(doc.raw_text)} total chars]"
        return {"doc_id": doc.doc_id, "title": doc.title, "doc_type": doc.doc_type,
                "source_name": doc.source_name, "filing_date": doc.filing_date,
                "source_url": doc.source_url, "text": text}

    def _tool_get_filing_section(self, doc_id: str, section_keyword: str) -> dict:
        chunks = self.store.get_chunks_for_doc(self.profile, doc_id)
        keyword_lower = section_keyword.lower()
        matching = [c for c in chunks if keyword_lower in c.text.lower()]
        if not matching:
            matching = chunks[:SECTION_FALLBACK_CHUNKS]
        doc = self.store.get_document(self.profile, doc_id)
        return {
            "doc_id": doc_id, "title": doc.title if doc else "unknown",
            "section_keyword": section_keyword,
            "matches": [{"text": c.text, "chunk_id": c.chunk_id, "page_or_section": c.page_or_section} for c in matching[:SECTION_MATCH_LIMIT]],
        }

    def _tool_compare_filings(self, doc_id_1: str, doc_id_2: str, topic: str) -> dict:
        sec1 = self._tool_get_filing_section(doc_id_1, topic)
        sec2 = self._tool_get_filing_section(doc_id_2, topic)
        doc1 = self.store.get_document(self.profile, doc_id_1)
        doc2 = self.store.get_document(self.profile, doc_id_2)
        return {
            "topic": topic,
            "filing_1": {"doc_id": doc_id_1, "title": doc1.title if doc1 else "unknown",
                         "doc_type": doc1.doc_type if doc1 else "unknown",
                         "filing_date": doc1.filing_date if doc1 else None,
                         "relevant_text": [m["text"] for m in sec1.get("matches", [])]},
            "filing_2": {"doc_id": doc_id_2, "title": doc2.title if doc2 else "unknown",
                         "doc_type": doc2.doc_type if doc2 else "unknown",
                         "filing_date": doc2.filing_date if doc2 else None,
                         "relevant_text": [m["text"] for m in sec2.get("matches", [])]},
        }

    def _tool_get_missing_materials(self) -> list[dict]:
        seen_types = {d.doc_type for d in self.snapshot.documents}
        doc_counts = {}
        for d in self.snapshot.documents:
            doc_counts[d.doc_type] = doc_counts.get(d.doc_type, 0) + 1
        return [
            {
                "material": mat.material,
                "criticality": mat.criticality,
                "severity": mat.severity,
                "why_it_matters": mat.why,
                "status": "Present" if mat.material in seen_types else "Missing",
                "actual_count": doc_counts.get(mat.material, 0),
                "min_expected": mat.min_count,
                "lookback_years": mat.lookback_years,
            }
            for mat in expected_materials_rich(self.profile)
        ]

    def _tool_get_corpus_status(self) -> dict:
        m = self.snapshot.manifest
        return {"ticker": m.ticker, "created_at": m.created_at, "last_refreshed_at": m.last_refreshed_at,
                "total_documents": m.total_documents, "total_chunks": m.total_chunks,
                "sources": [s.model_dump() for s in m.sources]}
