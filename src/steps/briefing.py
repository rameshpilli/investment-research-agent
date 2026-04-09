"""Phase 3: Analyst Briefing — Claude agent synthesizes Phase 1 + Phase 2."""

from __future__ import annotations

from src.models.companies import CompanyConfig, expected_materials_rich
from src.models.documents import CorpusSnapshot
from src.models.reports import (
    Phase3StructuredReport,
    citation_summary_for_model,
    enrich_citations_against_corpus,
    render_phase3_markdown,
    verify_citations_against_corpus,
)
from src.services.agent import ResearchAgent
from src.services.corpus_store import CorpusStore
from src.services.mcp_server import ResearchToolServer
from src.steps.common import atomic_write_text
from src.services.valuation import load_valuation_snapshot


def _format_money(value: float | int | None, currency: str) -> str:
    if value is None:
        return "N/A"
    prefix = "$" if currency == "USD" else f"{currency} "
    if value >= 1e9:
        return f"{prefix}{value/1e9:.1f}B"
    if value >= 1e6:
        return f"{prefix}{value/1e6:.0f}M"
    if value >= 1e3:
        return f"{prefix}{value/1e3:.0f}K"
    return f"{prefix}{value:.0f}"


def _minimal_fallback(
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    verdict: str,
    missing_materials: list[str],
) -> str:
    """Lightweight fallback — valuation snapshot + gaps.

    The real brief quality comes from the LLM synthesizing Phase 1 + Phase 2.
    """
    val = load_valuation_snapshot(profile)

    lines = [
        f"# Phase 3 Analyst Brief — {profile.name} ({profile.ticker})",
        "",
        f"Date: {snapshot.manifest.last_refreshed_at} | Verdict: {verdict}",
        "",
        "## Thesis Summary",
    ]

    if val:
        mc = val.get("market_cap")
        currency = val.get("currency") or profile.currency or ""
        mc_str = _format_money(mc, currency)
        sector = val.get("industry", val.get("sector", ""))
        descriptor = " ".join(part for part in [mc_str, sector, "company"] if part)
        parts = [f"{profile.name} is a {descriptor} on {profile.exchange}"]
        pct = val.get("pct_from_52w_high", 0)
        if pct > 20:
            parts.append(f"trading {pct:.0f}% below its 52-week high")
        fpe = val.get("forward_pe")
        if fpe:
            parts.append(f"forward P/E {fpe}")
        lines.append(". ".join(parts) + ".")
    else:
        lines.append(f"{profile.name} ({profile.exchange}). See Phase 2 dossier for full analysis.")

    lines.extend(["", "## Key Findings"])
    if val:
        currency = val.get("currency") or profile.currency or ""
        if val.get("total_debt") and val.get("total_cash"):
            debt = _format_money(val.get("total_debt"), currency)
            cash = _format_money(val.get("total_cash"), currency)
            lines.append(f"- **Financials**: Debt {debt}, Cash {cash}, D/E {val.get('debt_to_equity', 'N/A')}.")
        if val.get("analyst_rating"):
            target = _format_money(val.get("target_mean"), currency)
            lines.append(f"- **Analyst consensus**: {val['analyst_rating']} ({val.get('analyst_count', '?')} analysts), target {target}.")
        for label, key in [("1M", "return_1m"), ("3M", "return_3m"), ("1Y", "return_1y")]:
            if key in val:
                lines.append(f"- **{label} return**: {val[key]:+.1f}%")
                break
    else:
        lines.append("- Set ANTHROPIC_API_KEY for LLM-generated findings.")

    lines.extend(["", "## Top Risks"])
    flags = val.get("flags", []) if val else []
    if flags:
        for f in flags[:3]:
            lines.append(f"- {f}")
    else:
        lines.append("- See Phase 2 dossier for identified risks.")

    lines.extend(["", "## Critical Gaps"])
    if missing_materials:
        for m in expected_materials_rich(profile):
            if m.material in missing_materials and m.severity in ("Blocking", "Important"):
                lines.append(f"- **{m.material}** ({m.severity}): {m.why}")
    if lines[-1] == "## Critical Gaps":
        lines.append("- No blocking gaps.")

    lines.extend([
        "",
        "## Recommended Next Steps",
        "- Obtain missing materials before increasing conviction.",
        "- Run with ANTHROPIC_API_KEY for full Claude-powered analyst brief.",
        "",
        f"Sources: {snapshot.manifest.total_documents} documents | Full dossier: phase2_dossier.md",
    ])

    return "\n".join(lines)


async def build_phase3_brief(
    profile: CompanyConfig,
    snapshot: CorpusSnapshot,
    corpus_store: CorpusStore,
    agent: ResearchAgent,
    verdict: str,
    missing_materials: list[str],
    phase1_markdown: str = "",
    phase2_markdown: str = "",
    **ui_callbacks,
) -> dict[str, object]:
    fallback = _minimal_fallback(profile, snapshot, verdict, missing_materials)

    tool_server = ResearchToolServer(profile, corpus_store)
    run_result = await agent.run(
        phase="briefing",
        variables={
            "company_name": profile.name,
            "ticker": profile.ticker,
            "verdict": verdict,
            "missing_materials": ", ".join(missing_materials) or "None",
            "date": snapshot.manifest.last_refreshed_at,
            "phase1_summary": phase1_markdown[:2000] if phase1_markdown else "Phase 1 report not available.",
            "phase2_summary": phase2_markdown[:3000] if phase2_markdown else "Phase 2 dossier not available.",
        },
        tool_server=tool_server,
        fallback_markdown=fallback,
        output_schema=Phase3StructuredReport,
        on_tool_start=ui_callbacks.get("on_tool_start"),
        on_tool_end=ui_callbacks.get("on_tool_end"),
    )
    structured = None
    drafted = run_result.text
    citation_result: dict = {}
    if run_result.structured_output:
        structured = Phase3StructuredReport.model_validate(run_result.structured_output)
        structured = enrich_citations_against_corpus(structured, corpus_store, profile)
        citation_result = verify_citations_against_corpus(structured, corpus_store, profile)
        drafted = render_phase3_markdown(
            company_name=profile.name,
            ticker=profile.ticker,
            verdict=verdict,
            last_refreshed_at=snapshot.manifest.last_refreshed_at,
            document_count=snapshot.manifest.total_documents,
            report=structured,
            verification=citation_result,
        )
    elif run_result.error or not drafted.lstrip().startswith("# Phase 3 Analyst Brief"):
        drafted = fallback

    output_path = corpus_store.output_company_dir(profile) / "phase3_analyst_brief.md"
    atomic_write_text(output_path, drafted + "\n")

    return {
        "path": str(output_path),
        "markdown": drafted,
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
