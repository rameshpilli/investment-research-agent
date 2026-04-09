"""Phase 1: Ingestion & Gap Analysis — Claude agent with corpus tools."""

from __future__ import annotations

from src.models.companies import CompanyConfig, expected_materials_rich
from src.models.documents import CorpusSnapshot
from src.models.reports import (
    Phase1StructuredReport,
    citation_summary_for_model,
    enrich_citations_against_corpus,
    render_phase1_markdown,
    verify_citations_against_corpus,
)
from src.services.agent import ResearchAgent
from src.services.corpus_store import CorpusStore
from src.services.mcp_server import ResearchToolServer
from src.steps.common import atomic_write_text


def _minimal_fallback(profile: CompanyConfig, snapshot: CorpusSnapshot) -> tuple[str, list[str]]:
    """Lightweight fallback — just enough to show corpus status + gaps.

    The real report quality comes from the LLM path. This exists only
    so the pipeline doesn't crash when no API key is set.
    """
    seen = {d.doc_type for d in snapshot.documents}
    missing = [m.material for m in expected_materials_rich(profile) if m.material not in seen]

    lines = [
        f"# Phase 1 Ingestion Report — {profile.name} ({profile.ticker})",
        "",
        f"Documents: {snapshot.manifest.total_documents} | "
        f"Chunks: {snapshot.manifest.total_chunks} | "
        f"Last refreshed: {snapshot.manifest.last_refreshed_at}",
        "",
        "## Source Coverage",
        "| Source | Type | Date | Title |",
        "| --- | --- | --- | --- |",
    ]
    for d in sorted(snapshot.documents, key=lambda x: (x.filing_date or "", x.title))[:30]:
        lines.append(f"| {d.source_name} | {d.doc_type} | {d.filing_date or 'n/a'} | {d.title[:60]} |")

    lines.extend(["", "## Missing Materials"])
    for m in expected_materials_rich(profile):
        status = "Present" if m.material in seen else "**Missing**"
        lines.append(f"- {m.material} ({m.criticality}, {m.severity}): {status} — {m.why}")

    lines.append("")
    lines.append("*Note: This is a minimal fallback report. Set ANTHROPIC_API_KEY for full analysis.*")

    return "\n".join(lines), missing


async def build_phase1_report(
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    corpus_store: CorpusStore,
    agent: ResearchAgent,
    **ui_callbacks,
) -> dict[str, object]:
    fallback_md, missing_materials = _minimal_fallback(profile, snapshot)

    tool_server = ResearchToolServer(profile, corpus_store)
    run_result = await agent.run(
        phase="gap_analysis",
        variables={"company_name": profile.name, "ticker": profile.ticker},
        tool_server=tool_server,
        fallback_markdown=fallback_md,
        output_schema=Phase1StructuredReport,
        on_tool_start=ui_callbacks.get("on_tool_start"),
        on_tool_end=ui_callbacks.get("on_tool_end"),
    )
    structured = None
    drafted = run_result.text
    citation_result: dict = {}
    if run_result.structured_output:
        structured = Phase1StructuredReport.model_validate(run_result.structured_output)
        structured = enrich_citations_against_corpus(structured, corpus_store, profile)
        citation_result = verify_citations_against_corpus(structured, corpus_store, profile)
        drafted = render_phase1_markdown(
            company_name=profile.name,
            ticker=profile.ticker,
            report=structured,
            last_refreshed_at=snapshot.manifest.last_refreshed_at,
            verification=citation_result,
        )
    elif run_result.error or not drafted.lstrip().startswith("# Phase 1 Ingestion Report"):
        drafted = fallback_md

    output_path = corpus_store.output_company_dir(profile) / "phase1_ingestion_report.md"
    atomic_write_text(output_path, drafted + "\n")

    return {
        "path": str(output_path),
        "markdown": drafted,
        "missing_materials": missing_materials,
        "citation_verification": citation_result,
        "citation_summary": {} if structured is None else citation_summary_for_model(structured),
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
