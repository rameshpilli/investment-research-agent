#!/usr/bin/env python3
"""Step 2 Test: research tool server — each tool exercised independently against a fixture corpus."""

import json
from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, build_akso_docs, populate_store
from src.models import get_company
from src.services import mcp_http
from src.services.mcp_server import ResearchToolServer, TOOLS_ANTHROPIC


def main():
    soc = get_company("SOC US")
    akso = get_company("AKSO NO")

    with TemporaryDirectory() as tmp:
        soc_store, _ = populate_store(tmp + "/soc", soc, build_soc_docs(soc))
        akso_store, _ = populate_store(tmp + "/akso", akso, build_akso_docs(akso))

        section("Tool Definitions")
        tool_names = [t["name"] for t in TOOLS_ANTHROPIC]
        check(f"8 tools defined: {tool_names}", len(TOOLS_ANTHROPIC) == 8)
        for t in TOOLS_ANTHROPIC:
            check(f"  {t['name']} has description", len(t["description"]) > 10)
            check(f"  {t['name']} has input_schema", "input_schema" in t)

        # --- Global tool (no company needed) ---
        server = ResearchToolServer(soc, soc_store)

        section("list_available_companies")
        companies = json.loads(server.call("list_available_companies", {}))
        check(f"Returns {len(companies)} companies", len(companies) >= 2)
        tickers = [c["ticker"] for c in companies]
        check("SOC US in list", "SOC US" in tickers)
        check("AKSO NO in list", "AKSO NO" in tickers)
        check("Each has corpus_ready flag", all("corpus_ready" in c for c in companies))
        show("Companies", [(c["ticker"], c["name"], c["exchange"], c["corpus_ready"]) for c in companies])

        # --- SOC US ---
        section("search_corpus")
        result = json.loads(server.call("search_corpus", {"query": "revenue production barrels"}))
        check(f"Returned {len(result)} results", len(result) > 0)
        check("Results have text field", all("text" in r for r in result))
        check("Results have citation info", all("doc_id" in r and "source_name" in r for r in result))
        show("Top result", result[0]["text"][:150] if result else "NONE")

        result_filtered = json.loads(server.call("search_corpus", {"query": "revenue", "doc_type": "10-K"}))
        show(f"Filtered to 10-K: {len(result_filtered)} results", "")

        section("list_documents")
        docs = json.loads(server.call("list_documents", {}))
        check(f"Listed {len(docs)} documents", len(docs) == 5)
        check("Each has title, doc_type, source_name", all("title" in d and "doc_type" in d for d in docs))
        show("Documents", [(d["doc_type"], d["title"][:40]) for d in docs])

        docs_edgar = json.loads(server.call("list_documents", {"source_filter": "edgar"}))
        check(f"Filtered to edgar: {len(docs_edgar)} docs", len(docs_edgar) == 3)

        section("get_document")
        doc_id = docs[0]["doc_id"]
        doc = json.loads(server.call("get_document", {"doc_id": doc_id}))
        check("Got document", "text" in doc and len(doc["text"]) > 0)
        show("Document title", doc["title"])

        bad = json.loads(server.call("get_document", {"doc_id": "nonexistent"}))
        check("Nonexistent doc -> error", "error" in bad)

        section("get_filing_section")
        sec = json.loads(server.call("get_filing_section", {"doc_id": doc_id, "section_keyword": "revenue"}))
        check("Has matches", len(sec.get("matches", [])) > 0)
        show("Section matches", len(sec["matches"]))

        section("compare_filings")
        if len(docs) >= 2:
            cmp = json.loads(server.call("compare_filings", {
                "doc_id_1": docs[0]["doc_id"], "doc_id_2": docs[1]["doc_id"], "topic": "guidance"
            }))
            check("Comparison has two filings", "filing_1" in cmp and "filing_2" in cmp)
            check("Each has relevant_text", "relevant_text" in cmp["filing_1"])
            show("Filing 1 title", cmp["filing_1"]["title"])
            show("Filing 2 title", cmp["filing_2"]["title"])

        section("get_missing_materials")
        gaps = json.loads(server.call("get_missing_materials", {}))
        check(f"Returned {len(gaps)} materials", len(gaps) > 0)
        missing = [g for g in gaps if g["status"] == "Missing"]
        present = [g for g in gaps if g["status"] == "Present"]
        check(f"Present: {len(present)}, Missing: {len(missing)}", len(present) + len(missing) == len(gaps))
        show("Gaps", [(g["material"], g["status"], g["criticality"]) for g in gaps])

        section("get_corpus_status")
        status = json.loads(server.call("get_corpus_status", {}))
        check("Has ticker", status["ticker"] == "SOC US")
        check("Has doc count", status["total_documents"] == 5)
        check("Has source list", len(status["sources"]) > 0)
        show("Status", json.dumps(status, indent=2, default=str))

        section("Unknown tool")
        err = json.loads(server.call("nonexistent_tool", {}))
        check("Unknown tool -> error", "error" in err)

        section("HTTP MCP ticker resolution")

        class _Request:
            headers = {"x-research-ticker": "AKSO NO"}

        class _RequestContext:
            request = _Request()

        class _Context:
            request_context = _RequestContext()

        check("Explicit ticker wins", mcp_http._resolve_ticker("SOC US", _Context()) == "SOC US")
        check("Header ticker is accepted", mcp_http._resolve_ticker("", _Context()) == "AKSO NO")

        # Missing ticker should raise ValueError instead of silently falling back
        missing_ticker_raised = False
        try:
            mcp_http._resolve_ticker("")
        except ValueError:
            missing_ticker_raised = True
        check("Missing ticker raises ValueError", missing_ticker_raised)

        # Unsupported ticker should raise ValueError
        bad_ticker_raised = False
        try:
            mcp_http._resolve_ticker("FAKE XX")
        except ValueError:
            bad_ticker_raised = True
        check("Unsupported ticker raises ValueError", bad_ticker_raised)

        # --- AKSO NO ---
        section("AKSO NO — tool server cross-check")
        akso_server = ResearchToolServer(akso, akso_store)
        akso_docs = json.loads(akso_server.call("list_documents", {}))
        check(f"AKSO has {len(akso_docs)} documents", len(akso_docs) == 3)
        akso_gaps = json.loads(akso_server.call("get_missing_materials", {}))
        akso_missing = [g["material"] for g in akso_gaps if g["status"] == "Missing"]
        check("AKSO missing materials differ from SOC expectations", "10-K" not in [g["material"] for g in akso_gaps])
        show("AKSO missing", akso_missing)

    report()

if __name__ == "__main__":
    main()
