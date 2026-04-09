#!/usr/bin/env python3
"""Step 2 Test: Phase 1, 2, 3 outputs — deterministic fallback mode (no LLM key needed)."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, build_akso_docs, populate_store
from src.models import get_company
from src.services.agent import ResearchAgent
from src.steps.gap_analysis import build_phase1_report
from src.steps.dossier import build_phase2_dossier
from src.steps.briefing import build_phase3_brief


def main():
    soc = get_company("SOC US")
    akso = get_company("AKSO NO")
    agent = ResearchAgent(use_llm=False)

    with TemporaryDirectory() as tmp:
        soc_store, _ = populate_store(tmp + "/soc", soc, build_soc_docs(soc))
        akso_store, _ = populate_store(tmp + "/akso", akso, build_akso_docs(akso))

        # --- Phase 1 ---
        section("Phase 1 — SOC US")
        soc_snap = soc_store.load_corpus(soc)
        p1 = asyncio.run(build_phase1_report(soc, soc_snap, soc_store, agent))
        md1 = p1["markdown"]
        check("Has markdown", len(md1) > 50)
        check("Has Source Coverage", "Source Coverage" in md1)
        check("Has Missing Materials", "Missing" in md1)
        check(f"Missing: {p1['missing_materials']}", isinstance(p1["missing_materials"], list))
        check("File written", Path(p1["path"]).exists())
        show("Phase 1 (first 20 lines)", "\n".join(md1.splitlines()[:20]))

        # --- Phase 2 ---
        section("Phase 2 — SOC US")
        p2 = asyncio.run(build_phase2_dossier(soc, soc_snap, soc_store, agent, ["transcript", "competitor_filings"]))
        md2 = p2["markdown"]
        check("Has Executive Verdict", "Executive Verdict" in md2)
        check("Has Non-Consensus Risks", "Non-Consensus Risks" in md2)
        check("Has Contradictions", "Contradictions" in md2)
        check("Has What Would Change", "What Would Change" in md2)
        check("Has Information Gaps", "Information Gaps" in md2)
        check(f"Verdict: {p2['verdict']}", p2["verdict"] in ("Proceed", "Needs More Info", "Stop"))
        check("Fallback verdict is conservative", p2["verdict"] == "Needs More Info")
        check("transcript flagged as missing", "transcript" in md2)
        check("Has Thesis Summary", "Thesis Summary" in md2)
        check("Has Valuation Context", "Valuation Context" in md2)
        check("File written", Path(p2["path"]).exists())
        show("Phase 2 (first 30 lines)", "\n".join(md2.splitlines()[:30]))

        # --- Phase 3 ---
        section("Phase 3 — SOC US")
        p3 = asyncio.run(build_phase3_brief(soc, soc_snap, soc_store, agent, p2["verdict"], ["transcript"],
                                             phase1_markdown=md1, phase2_markdown=md2))
        md3 = p3["markdown"]
        check("Has Brief title", "Brief" in md3)
        check("Has Key Findings", "Key Findings" in md3)
        check("Has Top Risks", "Top Risks" in md3)
        check("Has Next Steps", "Next Steps" in md3)
        check("Has Critical Gaps", "Critical Gaps" in md3 or "Gaps" in md3)
        check("File written", Path(p3["path"]).exists())
        show("Phase 3 (first 20 lines)", "\n".join(md3.splitlines()[:20]))

        # --- AKSO ---
        section("Cross-company — AKSO NO")
        akso_snap = akso_store.load_corpus(akso)
        ap1 = asyncio.run(build_phase1_report(akso, akso_snap, akso_store, agent))
        check("AKSO Phase 1 mentions Aker Solutions", "Aker Solutions" in ap1["markdown"])

        ap2 = asyncio.run(build_phase2_dossier(akso, akso_snap, akso_store, agent, []))
        check("AKSO Phase 2 mentions Aker Solutions", "Aker Solutions" in ap2["markdown"])

        ap3 = asyncio.run(build_phase3_brief(akso, akso_snap, akso_store, agent, ap2["verdict"], [],
                                              phase1_markdown=ap1["markdown"], phase2_markdown=ap2["markdown"]))
        check("AKSO Phase 3 mentions Aker Solutions", "Aker Solutions" in ap3["markdown"])
        show("AKSO Phase 3 (first 10 lines)", "\n".join(ap3["markdown"].splitlines()[:10]))

    report()

if __name__ == "__main__":
    main()
