"""
Research Pipeline Orchestration
================================

Wires together the three-phase research workflow using plain async/await:

1. **load corpus from disk** -- reads pre-built corpus (no fetching)
2. **phase1_gap_analysis**   -- assess data completeness and coverage
3. **phase2_dossier**        -- generate an adversarial research dossier
4. **phase3_briefing**       -- synthesize a final analyst briefing

Phase 1 and Phase 2 run in parallel. Phase 3 waits for both.

The pipeline NEVER fetches data from the internet.  Users must run
Step 1 (--fetch) and Step 2 (--ingest) first to populate the corpus.
If no corpus exists, the pipeline bootstraps from input_files/ (the
pre-fetched files that ship with the repository).

Uses the Claude Agent SDK (via ResearchAgent) for Claude-powered research.
Falls back to deterministic template output when ANTHROPIC_API_KEY is not set.

Key objects:
- agent        -- the ResearchAgent instance (Claude Agent SDK)
- corpus_store -- shared CorpusStore used across steps
"""

from __future__ import annotations

import asyncio

from src.models import get_company
from src.models.companies import expected_materials
from src.services.agent import ResearchAgent
from src.services.corpus_store import CorpusStore
from src.steps import (
    answer_follow_up,
    build_phase1_report,
    build_phase2_dossier,
    build_phase3_brief,
    ensure_corpus_ready,
)

corpus_store = CorpusStore()
agent = ResearchAgent()


def normalize_pipeline_response(ticker: str, result: dict[str, object]) -> dict[str, object]:
    """Flatten the pipeline response into an app-friendly shape."""
    phase1 = result.get("phase1_result") or {}
    phase2 = result.get("phase2_result") or {}
    phase3 = result.get("phase3_result") or {}
    total_cost_usd = sum(
        float(phase.get("agent_run", {}).get("total_cost_usd") or 0.0)
        for phase in (phase1, phase2, phase3)
        if isinstance(phase, dict)
    )
    result["summary"] = {
        "ticker": ticker,
        "success": bool(result.get("success", False)),
        "corpus_status": result.get("corpus_status"),
        "verdict": phase2.get("verdict") if isinstance(phase2, dict) else None,
        "total_cost_usd": round(total_cost_usd, 4) if total_cost_usd else 0.0,
        "sessions": {
            "phase1": phase1.get("agent_run", {}).get("session_id") if isinstance(phase1, dict) else None,
            "phase2": phase2.get("agent_run", {}).get("session_id") if isinstance(phase2, dict) else None,
            "phase3": phase3.get("agent_run", {}).get("session_id") if isinstance(phase3, dict) else None,
        },
        "outputs": {
            "phase1": phase1.get("path") if isinstance(phase1, dict) else None,
            "phase2": phase2.get("path") if isinstance(phase2, dict) else None,
            "phase3": phase3.get("path") if isinstance(phase3, dict) else None,
        },
    }
    return result


async def run_pipeline(ticker: str) -> dict[str, object]:
    """Run the full 3-phase research pipeline for a company.

    1. Load corpus from disk (no fetching — uses whatever was saved by step 1)
    2. Phase 1 then Phase 2 sequentially (avoids concurrent access to local Qdrant/SQLite)
    3. Phase 3 runs after both (Claude-powered or deterministic fallback)
    """
    profile = get_company(ticker)

    # Load corpus — never fetches from the internet.
    # If no corpus exists, bootstraps from input_files/ (shipped with repo).
    snapshot, corpus_status = await ensure_corpus_ready(profile, corpus_store)

    # Compute missing materials for Phase 2
    seen_types = {doc.doc_type for doc in snapshot.documents}
    missing_materials = [mat for mat, _, _ in expected_materials(profile) if mat not in seen_types]

    # Layer 2: Research phases (Claude-powered)
    # Phase 1 then Phase 2 sequentially — parallel runs can race on local Qdrant file mode
    # and produce empty search results / bogus "corpus inaccessible" narratives.
    phase1_result = await build_phase1_report(profile, snapshot, corpus_store, agent)
    phase2_result = await build_phase2_dossier(
        profile, snapshot, corpus_store, agent, missing_materials,
    )

    # Phase 3 waits for both
    verdict = phase2_result.get("verdict", "Needs More Info") if isinstance(phase2_result, dict) else "Needs More Info"
    phase1_md = (phase1_result or {}).get("markdown", "")
    phase2_md = (phase2_result or {}).get("markdown", "")
    phase3_result = await build_phase3_brief(
        profile, snapshot, corpus_store, agent, verdict, missing_materials,
        phase1_markdown=phase1_md, phase2_markdown=phase2_md,
    )

    result = {
        "success": True,
        "corpus_status": corpus_status,
        "phase1_result": phase1_result,
        "phase2_result": phase2_result,
        "phase3_result": phase3_result,
    }
    return normalize_pipeline_response(ticker, result)


def run_pipeline_sync(ticker: str) -> dict[str, object]:
    """Synchronous wrapper for run_pipeline."""
    try:
        return asyncio.run(run_pipeline(ticker))
    finally:
        corpus_store.close()


def close_pipeline_resources() -> None:
    """Close shared pipeline resources for one-shot CLI commands."""
    corpus_store.close()


async def answer_question(ticker: str, question: str, conversation_id: str | None = None) -> str:
    """Answer a follow-up question against the existing corpus."""
    profile = get_company(ticker)
    if not corpus_store.corpus_exists(profile):
        # Bootstrap from input_files/ if needed
        await ensure_corpus_ready(profile, corpus_store)
    return await answer_follow_up(profile, corpus_store, question, agent, conversation_id=conversation_id)
