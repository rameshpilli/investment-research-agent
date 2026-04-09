#!/usr/bin/env python3
"""Step 1 Test: Models — company config, document creation, chunk creation, expected materials."""

from src.tests.helpers import section, check, show, report, make_doc
from src.models import get_company
from src.services.corpus_store import chunk_document
from src.steps.ingest import expected_materials


def main():
    section("Company Registry")
    soc = get_company("SOC US")
    check("SOC US → slug=soc_us, sec_registered=True", soc.slug == "soc_us" and soc.sec_registered)
    check("SOC connectors: edgar, yfinance, web_search", soc.enabled_connectors == ["edgar", "yfinance", "web_search"])
    show("SOC config", f"cik={soc.edgar_cik} yf={soc.yfinance_ticker}")

    akso = get_company("AKSO NO")
    check("AKSO NO → slug=akso_no, sec_registered=False", akso.slug == "akso_no" and not akso.sec_registered)
    check("AKSO connectors include newsweb + company_ir", "newsweb" in akso.enabled_connectors and "company_ir" in akso.enabled_connectors)
    show("AKSO config", f"newsweb_query={akso.newsweb_query} ir={akso.ir_url}")

    check("Alias SOC → SOC US", get_company("SOC").ticker == "SOC US")
    check("Alias AKSO.OL → AKSO NO", get_company("AKSO.OL").ticker == "AKSO NO")
    try:
        get_company("FAKE")
        check("Unknown ticker raises", False)
    except ValueError:
        check("Unknown ticker raises ValueError", True)

    section("Document Creation & Hashing")
    doc1 = make_doc(soc, "edgar", "SEC EDGAR", "10-K", "Test", "content A")
    doc2 = make_doc(soc, "edgar", "SEC EDGAR", "10-K", "Test", "content A")
    doc3 = make_doc(soc, "edgar", "SEC EDGAR", "10-K", "Test", "content B")
    check("Same inputs → same doc_id", doc1.doc_id == doc2.doc_id)
    check("Same identity, diff content → same doc_id but diff content_hash", doc1.doc_id == doc3.doc_id and doc1.content_hash != doc3.content_hash)

    section("Chunking")
    long_doc = make_doc(soc, "edgar", "SEC EDGAR", "10-K", "Long", "word " * 500)
    chunks = chunk_document(long_doc)
    check(f"Chunks created: {len(chunks)}", len(chunks) > 1)
    check("Sequential indices", [c.chunk_index for c in chunks] == list(range(len(chunks))))
    show("Chunk sizes", [len(c.text) for c in chunks])

    section("Expected Materials")
    soc_mats = [m[0] for m in expected_materials(soc)]
    akso_mats = [m[0] for m in expected_materials(akso)]
    check("SOC expects 10-K, 10-Q, 8-K", all(m in soc_mats for m in ["10-K", "10-Q", "8-K"]))
    check("AKSO expects regulatory_announcement, annual_report", all(m in akso_mats for m in ["regulatory_announcement", "annual_report"]))
    check("AKSO does NOT expect 10-K", "10-K" not in akso_mats)

    report()

if __name__ == "__main__":
    main()
