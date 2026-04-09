"""
HTTP MCP Server for external clients (Chainlit UI, Claude Desktop, Cursor).

Exposes the same 8 research tools as the in-process SDK tool layer, but
over Streamable HTTP so any MCP-compatible client can connect.

The server accepts a ``ticker`` argument on corpus-specific tools, and can
also infer the company from the ``X-Research-Ticker`` request header. It
lazily creates a ``ResearchToolServer`` per company on first use.

Run as standalone service (used in docker-compose):
    python -m src.services.mcp_http

Default endpoint: http://0.0.0.0:8081/mcp
"""

from __future__ import annotations

import atexit
import logging
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from src.config import settings
from src.models.companies import COMPANIES, get_company
from src.services.corpus_store import CorpusStore
from src.services.mcp_server import ResearchToolServer

logger = logging.getLogger(__name__)

_corpus_store = CorpusStore()
_tool_servers: dict[str, ResearchToolServer] = {}

atexit.register(_corpus_store.close)


def _get_tool_server(ticker: str) -> ResearchToolServer:
    """Get or create a tool server for a company."""
    key = ticker.upper()
    if key not in _tool_servers:
        profile = get_company(key)
        _tool_servers[key] = ResearchToolServer(profile, _corpus_store)
    return _tool_servers[key]


_SUPPORTED_TICKERS = set(COMPANIES.keys())
_SUPPORTED_TICKERS_STR = ", ".join(sorted(_SUPPORTED_TICKERS))


def _resolve_ticker(ticker: str, ctx: Context | None = None) -> str:
    """Resolve ticker from argument, request header, or raise."""
    resolved = ticker.strip().upper() if ticker else ""

    if not resolved and ctx is not None:
        request = ctx.request_context.request
        if request is not None:
            resolved = request.headers.get("x-research-ticker", "").strip().upper()

    if not resolved:
        raise ValueError(
            f"No ticker provided. Pass a ticker argument or set the "
            f"X-Research-Ticker header. Supported tickers: {_SUPPORTED_TICKERS_STR}"
        )

    if resolved not in _SUPPORTED_TICKERS:
        raise ValueError(
            f"Ticker '{resolved}' is not supported. Available tickers: {_SUPPORTED_TICKERS_STR}"
        )

    return resolved


# ---------------------------------------------------------------------------
# FastMCP server definition
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="investment-research",
    instructions=(
        "Investment research tools for analyzing company filings, "
        "searching the ingested corpus, detecting tone shifts, and "
        "identifying information gaps.  Pass a ticker (e.g. 'SOC US') "
        "to target a specific company."
    ),
    host="0.0.0.0",
    port=int(settings.mcp_port),
)


@mcp.tool()
def list_available_companies() -> str:
    """List all companies available for research with their corpus status."""
    # This tool is company-agnostic — use the first configured ticker to get a tool server.
    first_ticker = next(iter(COMPANIES))
    server = _get_tool_server(first_ticker)
    return server.call("list_available_companies", {})


@mcp.tool()
def search_corpus(query: str, ticker: str = "", limit: int = 8, doc_type: str = "", ctx: Context | None = None) -> str:
    """Semantic search over stored document chunks. Returns ranked results with citations."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    args: dict[str, Any] = {"query": query, "limit": limit}
    if doc_type:
        args["doc_type"] = doc_type
    return server.call("search_corpus", args)


@mcp.tool()
def list_documents(ticker: str = "", source_filter: str = "", ctx: Context | None = None) -> str:
    """List all documents in the corpus for a company."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    args: dict[str, Any] = {}
    if source_filter:
        args["source_filter"] = source_filter
    return server.call("list_documents", args)


@mcp.tool()
def get_document(doc_id: str, ticker: str = "", ctx: Context | None = None) -> str:
    """Get full text and metadata for a specific document by doc_id."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    return server.call("get_document", {"doc_id": doc_id})


@mcp.tool()
def get_filing_section(doc_id: str, section_keyword: str, ticker: str = "", ctx: Context | None = None) -> str:
    """Search within a specific document for a section or topic."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    return server.call("get_filing_section", {"doc_id": doc_id, "section_keyword": section_keyword})


@mcp.tool()
def compare_filings(doc_id_1: str, doc_id_2: str, topic: str, ticker: str = "", ctx: Context | None = None) -> str:
    """Compare how two documents discuss the same topic. Use for tone shift detection."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    return server.call("compare_filings", {"doc_id_1": doc_id_1, "doc_id_2": doc_id_2, "topic": topic})


@mcp.tool()
def get_missing_materials(ticker: str = "", ctx: Context | None = None) -> str:
    """What expected document types are missing from the corpus?"""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    return server.call("get_missing_materials", {})


@mcp.tool()
def get_corpus_status(ticker: str = "", ctx: Context | None = None) -> str:
    """Get corpus metadata: when built, last refreshed, source freshness, document and chunk counts."""
    ticker = _resolve_ticker(ticker, ctx)
    server = _get_tool_server(ticker)
    return server.call("get_corpus_status", {})


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
