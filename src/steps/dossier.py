"""Phase 2: Adversarial Dossier + Follow-up Q&A — Claude agent with corpus tools."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

from src.config import settings
from src.models.companies import CompanyConfig, expected_materials_rich
from src.models.documents import Chunk, CorpusSnapshot
from src.models.reports import (
    FollowUpStructuredAnswer,
    Phase2StructuredReport,
    citation_summary_for_model,
    enrich_citations_against_corpus,
    render_followup_markdown,
    render_phase2_markdown,
    validate_phase2_report,
    verify_citations_against_corpus,
)
from src.services.agent import ResearchAgent
from src.services.corpus_store import CorpusStore
from src.services.mcp_server import ResearchToolServer
from src.services.valuation import load_valuation_snapshot, valuation_snapshot_text
from src.steps.common import atomic_write_text, make_citation

TOKEN_RE = re.compile(r"[a-zA-Z]{3,}")

_STOP_WORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "has", "her",
    "was", "one", "our", "out", "how", "its", "may", "that", "this", "with",
    "have", "from", "been", "were", "what", "when", "where", "which", "about",
    "does", "they", "their", "will", "would", "could", "should", "also", "than",
    "into", "each", "other", "more", "some", "such", "these", "those",
})


def _fallback_has_minimum_evidence(
    snapshot: CorpusSnapshot,
    missing_materials: list[str],
    profile: CompanyConfig,
) -> tuple[bool, str]:
    """Guardrail for deterministic fallback verdicts.

    Fallback reports should default to "Needs More Info" unless the corpus has
    enough primary evidence and no blocking gaps.
    """
    if snapshot.manifest.total_documents < 6:
        return False, "Insufficient primary evidence in corpus for a decisive verdict."
    materials = expected_materials_rich(profile)
    blocking = [m for m in materials if m.material in missing_materials and m.severity == "Blocking"]
    if blocking:
        return False, f"Blocking materials are missing: {', '.join(m.material for m in blocking)}."
    if len(missing_materials) >= 2:
        return False, f"Too many material gaps remain ({len(missing_materials)} missing)."
    return True, "Minimum evidence threshold passed for a provisional fallback verdict."


def _verdict(snapshot: CorpusSnapshot, missing_materials: list[str], profile: CompanyConfig) -> tuple[str, str]:
    """Compute fallback verdict with strict evidence gate.

    Even with enough corpus coverage, deterministic fallback does not perform
    adversarial contradiction/tone-shift analysis with source-linked citations.
    Therefore it should not emit decisive Proceed/Stop outcomes.
    """
    meets_threshold, reason = _fallback_has_minimum_evidence(snapshot, missing_materials, profile)
    if not meets_threshold:
        return "Needs More Info", reason
    return (
        "Needs More Info",
        "Deterministic fallback cannot issue a decisive verdict without full citation-backed adversarial analysis. "
        f"{reason}",
    )


def _minimal_fallback(
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    missing_materials: list[str],
) -> tuple[str, str]:
    """Lightweight fallback with valuation snapshot.

    The real report quality comes from the LLM path. This just provides
    basic structure + market data so the output isn't empty.
    """
    verdict, evidence_reason = _verdict(snapshot, missing_materials, profile)
    val_snapshot = load_valuation_snapshot(profile)
    val_text = valuation_snapshot_text(val_snapshot) if val_snapshot else "No market data available."

    # Build a one-line verdict rationale
    if verdict == "Needs More Info":
        rationale = evidence_reason
    else:
        rationale = f"{evidence_reason} This remains a provisional fallback assessment."

    lines = [
        f"# Phase 2 Deep Research Dossier — {profile.name} ({profile.ticker})",
        "",
        "## Executive Verdict",
        f"**{verdict}**. {rationale}",
        "",
        "## Thesis Summary",
        f"{profile.name} ({profile.exchange}) — see valuation context below for market positioning. Full thesis requires LLM analysis of filings. [UNVERIFIED]",
        "",
        "## Company Overview",
        f"Corpus contains {len(snapshot.documents)} documents. Set ANTHROPIC_API_KEY for a grounded business overview.",
        "",
        "## Valuation Context",
        val_text,
        "",
        "## Top Non-Consensus Risks",
        "- Deterministic fallback does not support strong risk ranking; run with LLM for evidence-backed non-consensus risks. [UNVERIFIED]",
        "",
        "## Contradictions & Tone Shifts",
        "- Deterministic fallback does not perform cross-document contradiction analysis. [UNVERIFIED]",
        "",
        "## What Would Change This Verdict",
        "- Evidence of sustained positive cash flow and successful operational execution.",
        "- Resolution of key regulatory uncertainties.",
        "- Refinancing or deleveraging that reduces financial risk.",
        "",
        "## Information Gaps",
    ]
    if missing_materials:
        for m in expected_materials_rich(profile):
            if m.material in missing_materials:
                lines.append(f"- **{m.material}** ({m.severity}): {m.why}")
    else:
        lines.append("- No major gaps identified.")

    lines.extend(["", "*Note: This is a minimal fallback. Set ANTHROPIC_API_KEY for full adversarial analysis.*"])

    return "\n".join(lines), verdict


def _has_question_overlap(question: str, chunks: list[Chunk]) -> bool:
    query_tokens = {t.lower() for t in TOKEN_RE.findall(question) if t.lower() not in _STOP_WORDS}
    if not query_tokens:
        return bool(chunks)
    for chunk in chunks:
        chunk_tokens = {t.lower() for t in TOKEN_RE.findall(chunk.text)}
        if query_tokens & chunk_tokens:
            return True
    return False


async def build_phase2_dossier(
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    corpus_store: CorpusStore,
    agent: ResearchAgent,
    missing_materials: list[str],
    **ui_callbacks,
) -> dict[str, object]:
    fallback_md, fallback_verdict = _minimal_fallback(profile, snapshot, missing_materials)

    tool_server = ResearchToolServer(profile, corpus_store)
    run_result = await agent.run(
        phase="dossier",
        variables={"company_name": profile.name, "ticker": profile.ticker},
        tool_server=tool_server,
        fallback_markdown=fallback_md,
        output_schema=Phase2StructuredReport,
        on_tool_start=ui_callbacks.get("on_tool_start"),
        on_tool_end=ui_callbacks.get("on_tool_end"),
    )

    structured = None
    drafted = run_result.text
    verdict = fallback_verdict
    citation_result: dict = {}

    if run_result.structured_output:
        structured = Phase2StructuredReport.model_validate(run_result.structured_output)
        structured = enrich_citations_against_corpus(structured, corpus_store, profile)
        citation_result = verify_citations_against_corpus(structured, corpus_store, profile)
        drafted = render_phase2_markdown(profile.name, profile.ticker, structured, verification=citation_result)
        verdict = structured.executive_verdict
    elif run_result.error or not drafted.lstrip().startswith("# Phase 2 Deep Research Dossier"):
        drafted = fallback_md
    elif drafted != fallback_md:
        # LLM ran but no structured output — extract verdict from text
        if re.search(r'\bStop\b', drafted[:500]):
            verdict = "Stop"
        elif re.search(r'\bProceed\b', drafted[:500]):
            verdict = "Proceed"
        else:
            verdict = "Needs More Info"

    validation_errors = validate_phase2_report(structured) if structured else []

    output_path = corpus_store.output_company_dir(profile) / "phase2_dossier.md"
    atomic_write_text(output_path, drafted + "\n")

    return {
        "path": str(output_path),
        "markdown": drafted,
        "verdict": verdict,
        "validation_errors": validation_errors,
        "citation_verification": citation_result,
        "citation_summary": citation_summary_for_model(structured) if structured else {},
        "agent_run": {
            "session_id": run_result.session_id,
            "total_cost_usd": run_result.total_cost_usd,
            "usage": run_result.usage,
            "model_usage": run_result.model_usage,
            "stop_reason": run_result.stop_reason,
            "num_turns": run_result.num_turns,
            "tool_calls": run_result.tool_calls,
            "is_fallback": run_result.is_fallback,
            "error": run_result.error,
        },
    }


async def answer_follow_up(
    profile: CompanyConfig,
    corpus_store: CorpusStore,
    question: str,
    agent: ResearchAgent,
    conversation_id: str | None = None,
) -> str:
    try:
        snapshot = corpus_store.load_corpus(profile)
    except FileNotFoundError:
        return "No corpus available for this company. Please run data ingestion first."

    # Build a lightweight fallback from a direct search so the agent has
    # something to return even if the SDK call fails.  This is *not* a
    # gate — the agent always gets a chance to search via MCP tools.
    fallback = f"Question: {question}\n\nNo pre-fetched evidence available."
    try:
        chunks = corpus_store.search_chunks(profile, question, limit=settings.followup_retrieval_limit)
        documents_by_id = {d.doc_id: d for d in snapshot.documents}
        citations = []
        for chunk in chunks[:4]:
            doc = documents_by_id.get(chunk.doc_id)
            if doc:
                citations.append(make_citation(chunk, doc))
        if citations:
            evidence_lines = [
                f"- {c['content']} ({c['source_name']}, {c.get('doc_type', 'document')})"
                for c in citations
            ]
            fallback = "\n".join([
                f"Question: {question}",
                "",
                "Based on the stored corpus, the most relevant evidence is:",
                *evidence_lines,
                "",
                "Citations:",
                *[f"[{i+1}] {c['source_name']} ({c.get('doc_type', 'document')}, {c.get('filing_date', 'n/a')})" for i, c in enumerate(citations)],
            ])
    except Exception as e:
        logger.warning("Pre-fetch for fallback failed for %s: %s", profile.ticker, e)

    tool_server = ResearchToolServer(profile, corpus_store)
    run_result = await agent.run_followup(
        variables={"company_name": profile.name, "ticker": profile.ticker, "question": question},
        tool_server=tool_server,
        fallback_markdown=fallback,
        conversation_id=conversation_id,
        output_schema=FollowUpStructuredAnswer,
    )
    if run_result.structured_output:
        structured = FollowUpStructuredAnswer.model_validate(run_result.structured_output)
        return render_followup_markdown(structured)
    return run_result.text
