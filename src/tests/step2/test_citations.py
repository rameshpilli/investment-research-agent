#!/usr/bin/env python3
"""Step 2 Test: Citation wiring — chunk->citation, inline markers, retrieval, deep verification."""

from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, populate_store
from src.models import get_company
from src.models.reports import (
    CitedBlock,
    Phase2StructuredReport,
    Phase2RiskItem,
    ReportCitation,
    ReportFinding,
    verify_citations_against_corpus,
)
from src.steps.common import (
    make_citation, append_citations, citation_inline,
    citation_summary,
    retrieve_citations, to_report_citation,
)


def main():
    soc = get_company("SOC US")

    with TemporaryDirectory() as tmp:
        store, _ = populate_store(tmp, soc, build_soc_docs(soc))
        snap = store.load_corpus(soc)

        section("chunk -> citation dict")
        doc = snap.documents[0]
        chunk = snap.chunks[0]
        cit = make_citation(chunk, doc)
        check("source_name set", cit["source_name"] == doc.source_name)
        check("document_id set", cit["document_id"] == doc.doc_id)
        check("url set", cit["url"] == doc.source_url)
        check("has chunk_id", "chunk_id" in cit)
        check("has doc_type", "doc_type" in cit)
        check("has filing_date", "filing_date" in cit)
        show("Citation dict keys", list(cit.keys()))

        section("dict -> ReportCitation conversion")
        rc = to_report_citation(cit)
        check("ReportCitation source_name matches", rc.source_name == cit["source_name"])
        check("ReportCitation doc_id set", rc.doc_id == cit["document_id"])
        check("ReportCitation chunk_id set", rc.chunk_id == cit["chunk_id"])
        check("ReportCitation excerpt set", rc.excerpt is not None)

        section("Inline Markers")
        marker = citation_inline(cit, 1)
        check("Inline marker has [1:", "[1:" in marker)
        check("Inline marker has source name", cit["source_name"] in marker)

        section("append_citations helper")
        check("Adds marker when citations present", "[1:" in append_citations("Claim.", [cit]))
        check("[UNVERIFIED] when no citations", "[UNVERIFIED]" in append_citations("Claim.", []))

        section("Citation Summary (heuristic)")
        summary = citation_summary([cit, cit])
        check(f"Total: {summary['total']}", summary["total"] == 2)
        check(f"Verified: {summary['verified']}", summary["verified"] == 2)
        check("Has traceable fields", summary["verified"] > 0)

        section("Semantic Retrieval -> Citations")
        found = retrieve_citations("pipeline permits regulatory", soc, snap, store, limit=3)
        check(f"Found {len(found)} citations", len(found) > 0)

        # ── Deep verification against corpus ──
        section("Deep Citation Verification")

        # Build a Phase2StructuredReport with real citations
        report_citation = to_report_citation(cit)
        test_report = Phase2StructuredReport(
            executive_verdict="Needs More Info",
            verdict_rationale=CitedBlock(text="Test rationale.", citations=[report_citation]),
            thesis_summary=CitedBlock(text="Test thesis.", citations=[report_citation]),
            company_overview=CitedBlock(text="Test overview.", citations=[]),
            top_nonconsensus_risks=[
                Phase2RiskItem(
                    title="Test Risk",
                    direction="Downside",
                    analysis=CitedBlock(text="Risk detail.", citations=[report_citation]),
                ),
            ],
            information_gaps=[
                ReportFinding(title="Gap", analysis=CitedBlock(text="Missing stuff.", citations=[])),
            ],
        )

        # Run deep verification
        result = verify_citations_against_corpus(test_report, store, soc)
        check(f"Deep verify total: {result['total']}", result["total"] >= 2)
        check(f"Deep verify verified: {result['verified']}", result["verified"] >= 1)
        check(f"Deep verify ungrounded: {result['ungrounded']}", isinstance(result["ungrounded"], int))
        check("Has details list", isinstance(result["details"], list))
        check("Details match total", len(result["details"]) == result["total"])
        show("Verification rate", f"{result['verification_rate']:.0%}")
        for d in result["details"][:3]:
            show(f"  [{d['status']}]", f"{d['citation']}: {d['reason']}")

        # Test with a fabricated citation (should be unverified_traceable — real doc, fake excerpt)
        section("Deep Verification — fabricated citation")
        fake_citation = ReportCitation(
            source_name="Fabricated",
            doc_type="10-K",
            doc_id=doc.doc_id,  # real doc_id
            excerpt="This text does not appear anywhere in the filing at all xyz123",
        )
        fake_report = Phase2StructuredReport(
            executive_verdict="Stop",
            verdict_rationale=CitedBlock(text="Fake.", citations=[fake_citation]),
            thesis_summary=CitedBlock(text="Fake thesis."),
            company_overview=CitedBlock(text="Fake overview."),
            top_nonconsensus_risks=[],
            information_gaps=[],
        )
        fake_result = verify_citations_against_corpus(fake_report, store, soc)
        check("Fabricated excerpt detected as not verified", fake_result["verified"] == 0)
        # Real doc_id + fake excerpt → unverified_traceable (doc exists, excerpt doesn't match)
        check(
            f"Unverified traceable: {fake_result['unverified_traceable']}",
            fake_result["unverified_traceable"] >= 1,
        )
        check("Has unverified_traceable key", "unverified_traceable" in fake_result)
        if fake_result["details"]:
            show("Fabricated detail", fake_result["details"][0])

        # Test with a completely bogus doc_id (should be plain unverified)
        section("Deep Verification — bogus doc_id citation")
        bogus_citation = ReportCitation(
            source_name="Bogus",
            doc_type="10-K",
            doc_id="nonexistent_doc_id_abc123",
            excerpt="This text does not exist",
        )
        bogus_report = Phase2StructuredReport(
            executive_verdict="Stop",
            verdict_rationale=CitedBlock(text="Bogus.", citations=[bogus_citation]),
            thesis_summary=CitedBlock(text="Bogus thesis."),
            company_overview=CitedBlock(text="Bogus overview."),
            top_nonconsensus_risks=[],
            information_gaps=[],
        )
        bogus_result = verify_citations_against_corpus(bogus_report, store, soc)
        check("Bogus doc_id detected as unverified", bogus_result["unverified"] >= 1)
        check("Not unverified_traceable", bogus_result["unverified_traceable"] == 0)

        # Test with no corpus (graceful degradation)
        section("Deep Verification — no corpus")
        no_corpus_result = verify_citations_against_corpus(test_report, None, None)
        check("Works without corpus", no_corpus_result["total"] >= 2)

        store.close()

    report()

if __name__ == "__main__":
    main()
