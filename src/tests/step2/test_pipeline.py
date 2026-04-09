#!/usr/bin/env python3
"""Step 2 Test: Pipeline response normalization for API, CLI, and UI callers."""

from src.tests.helpers import section, check, show, report
from src.pipeline import normalize_pipeline_response


def main():
    section("Pipeline — Direct Result")
    raw_result = {
        "success": True,
        "corpus_status": "prepared",
        "phase1_result": {
            "path": "output/soc_us/phase1_ingestion_report.md",
            "markdown": "# Phase 1",
            "agent_run": {"session_id": "phase1-session", "total_cost_usd": 0.125},
        },
        "phase2_result": {
            "path": "output/soc_us/phase2_dossier.md",
            "markdown": "# Phase 2",
            "verdict": "Watch",
            "agent_run": {"session_id": "phase2-session", "total_cost_usd": 0.375},
        },
        "phase3_result": {
            "path": "output/soc_us/phase3_analyst_brief.md",
            "markdown": "# Phase 3",
            "agent_run": {"session_id": "phase3-session", "total_cost_usd": 0.25},
        },
    }
    normalized = normalize_pipeline_response("SOC US", raw_result)
    check("Corpus status preserved", normalized.get("corpus_status") == "prepared")
    check("Phase 1 result exposed", normalized.get("phase1_result", {}).get("path") is not None)
    check("Phase 2 result exposed", normalized.get("phase2_result", {}).get("verdict") == "Watch")
    check("Phase 3 result exposed", normalized.get("phase3_result", {}).get("path") is not None)
    check("Summary has ticker", normalized.get("summary", {}).get("ticker") == "SOC US")
    check("Summary has verdict", normalized.get("summary", {}).get("verdict") == "Watch")
    check("Summary totals Claude cost", normalized.get("summary", {}).get("total_cost_usd") == 0.75)
    check("Summary exposes session ids", normalized.get("summary", {}).get("sessions", {}).get("phase2") == "phase2-session")
    show("Summary", normalized.get("summary"))

    section("Pipeline — Missing Data Safety")
    normalized = normalize_pipeline_response("SOC US", {"success": False})
    summary = normalized.get("summary", {})
    check("Summary keeps ticker", summary.get("ticker") == "SOC US")
    check("Summary keeps success flag", summary.get("success") is False)
    check("Outputs stay nullable", summary.get("outputs", {}).get("phase1") is None)
    check("Cost defaults to zero", summary.get("total_cost_usd") == 0.0)

    report()


if __name__ == "__main__":
    main()
