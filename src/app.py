"""
Chainlit Chat UI — Agentic View
==========================================

Interactive front-end for the investment research platform.

Shows per-phase agent steps with nested tool calls, real-time progress,
and structured run summaries so the analyst can follow the research
process as it happens.

Flow:
    1. Analyst types a ticker (e.g. "SOC US")
    2. If reports already exist on disk → load and display instantly (no LLM cost)
    3. If no reports exist → run the full pipeline with agentic step visibility
    4. After reports are displayed, follow-up questions search the corpus only
"""

from __future__ import annotations

import logging
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    import chainlit as cl
except ImportError:  # pragma: no cover
    cl = None

from src.config import settings
from src.models import get_company, list_supported_tickers
from src.models.companies import expected_materials


def _welcome_mcp_block() -> str:
    """Surface MCP connectivity in the UI so analysts know how corpus tools are reached."""
    port = settings.mcp_port
    configured = (settings.mcp_url or "").strip()
    if configured:
        return (
            "### MCP server (corpus tools)\n"
            f"- **Agents in this UI** use: `{configured}`\n"
            f"- **External MCP clients** on your laptop (Claude Desktop, Cursor, etc.): "
            f"`http://localhost:{port}/mcp` — pass ticker via `X-Research-Ticker` or tool arguments.\n"
        )
    return (
        "### MCP server (corpus tools)\n"
        "- **Not set** — `RESEARCH_MCP_URL` is empty, so corpus tools run **in-process** in this app.\n"
        f"- To use the HTTP MCP server instead, set `RESEARCH_MCP_URL` (Docker Compose sets it to the `mcp` service, e.g. `http://localhost:{port}/mcp` from the host).\n"
    )


def _new_conversation_id() -> str:
    return uuid.uuid4().hex


def _tool_display_name(raw: str) -> str:
    """Strip MCP prefix for display: mcp__research-corpus__search_corpus -> search_corpus"""
    if "__" in raw:
        return raw.rsplit("__", 1)[-1]
    return raw


# ---------------------------------------------------------------------------
# Cached report loading (no LLM cost)
# ---------------------------------------------------------------------------

def _load_cached_reports(ticker: str) -> dict | None:
    """Check if all 3 phase reports exist on disk and return them.

    Returns None if any report is missing (pipeline needs to run).
    """
    from src.services.corpus_store import CorpusStore

    profile = get_company(ticker)
    corpus_store = CorpusStore()

    try:
        if not corpus_store.corpus_exists(profile):
            logger.warning("Cache miss for %s: corpus does not exist at %s", ticker, corpus_store.corpus_dir(profile))
            return None

        output_dir = corpus_store.output_company_dir(profile)
        phase_files = {
            "phase1": output_dir / "phase1_ingestion_report.md",
            "phase2": output_dir / "phase2_dossier.md",
            "phase3": output_dir / "phase3_analyst_brief.md",
        }

        # All three must exist and be non-empty
        for name, path in phase_files.items():
            if not path.exists():
                logger.warning("Cache miss for %s: %s not found at %s", ticker, name, path)
                return None
            if path.stat().st_size < 50:
                logger.warning("Cache miss for %s: %s too small (%d bytes) at %s", ticker, name, path.stat().st_size, path)
                return None

        logger.info("Cache hit for %s — loading 3 reports from %s", ticker, output_dir)
        return {
            "phase1_result": {"markdown": phase_files["phase1"].read_text(encoding="utf-8").strip(), "path": str(phase_files["phase1"])},
            "phase2_result": {"markdown": phase_files["phase2"].read_text(encoding="utf-8").strip(), "path": str(phase_files["phase2"])},
            "phase3_result": {"markdown": phase_files["phase3"].read_text(encoding="utf-8").strip(), "path": str(phase_files["phase3"])},
            "cached": True,
        }
    except Exception as exc:
        logger.exception("Cache check failed for %s: %s", ticker, exc)
        return None
    finally:
        corpus_store.close()


# ---------------------------------------------------------------------------
# In-process pipeline with agentic step visibility
# ---------------------------------------------------------------------------

async def _run_pipeline_with_steps(ticker: str) -> dict:
    """Run the 3-phase pipeline with per-phase Chainlit Steps and tool-call nesting."""
    from src.services.agent import ResearchAgent
    from src.services.corpus_store import CorpusStore

    profile = get_company(ticker)
    corpus_store = CorpusStore()
    agent = ResearchAgent()

    try:
        return await _run_pipeline_inner(ticker, profile, corpus_store, agent)
    finally:
        corpus_store.close()


async def _run_pipeline_inner(ticker: str, profile, corpus_store, agent) -> dict:
    from src.steps import (
        build_phase1_report,
        build_phase2_dossier,
        build_phase3_brief,
        ensure_corpus_ready,
    )

    # -- Corpus prep --------------------------------------------------------
    async with cl.Step(name="Preparing Corpus", type="tool") as corpus_step:
        corpus_step.input = {"ticker": ticker, "connectors": profile.enabled_connectors}
        try:
            snapshot, corpus_status = await ensure_corpus_ready(profile, corpus_store)
        except FileNotFoundError as exc:
            # Handle here so Chainlit does not also emit a second error bubble for the same failure.
            corpus_step.output = "No processed corpus on disk — fetch/ingest required (see message below)."
            return {"corpus_error": exc.args[0] if exc.args else f"No corpus for {ticker}."}
        corpus_step.output = (
            f"{snapshot.manifest.total_documents} documents, "
            f"{snapshot.manifest.total_chunks} chunks from "
            f"{', '.join(profile.enabled_connectors)}"
        )

    seen_types = {doc.doc_type for doc in snapshot.documents}
    missing_materials = [mat for mat, _, _ in expected_materials(profile) if mat not in seen_types]

    # -- Phase 1 then Phase 2 sequentially (matches pipeline.py; safe for local Qdrant) -----
    phase1_result = await _run_phase_with_steps(
        phase_label="Phase 1: Ingestion & Gap Analysis",
        agent_name="gap-analyst",
        coro_fn=build_phase1_report,
        coro_args=(profile, snapshot, corpus_store, agent),
    )
    phase2_result = await _run_phase_with_steps(
        phase_label="Phase 2: Adversarial Dossier",
        agent_name="adversarial-researcher",
        coro_fn=build_phase2_dossier,
        coro_args=(profile, snapshot, corpus_store, agent, missing_materials),
    )

    # -- Phase 3: Analyst Brief (briefing-writer) — waits for both ---------
    verdict = phase2_result.get("verdict", "Needs More Info") if isinstance(phase2_result, dict) else "Needs More Info"
    phase1_md = (phase1_result or {}).get("markdown", "")
    phase2_md = (phase2_result or {}).get("markdown", "")

    phase3_result = await _run_phase_with_steps(
        phase_label="Phase 3: Analyst Briefing",
        agent_name="briefing-writer",
        coro_fn=build_phase3_brief,
        coro_args=(profile, snapshot, corpus_store, agent, verdict, missing_materials),
        coro_kwargs={"phase1_markdown": phase1_md, "phase2_markdown": phase2_md},
    )

    return {
        "phase1_result": phase1_result,
        "phase2_result": phase2_result,
        "phase3_result": phase3_result,
        "corpus_status": corpus_status,
    }


async def _run_phase_with_steps(
    *,
    phase_label: str,
    agent_name: str,
    coro_fn,
    coro_args: tuple,
    coro_kwargs: dict | None = None,
) -> dict:
    """Run a single research phase wrapped in a Chainlit Step with nested tool calls."""
    coro_kwargs = coro_kwargs or {}

    async with cl.Step(name=f"{agent_name} | {phase_label}", type="run") as phase_step:
        phase_step.input = {"agent": agent_name, "phase": phase_label}

        # Track tool calls as nested steps
        tool_steps: dict[str, cl.Step] = {}

        async def on_tool_start(tool_name: str, args_summary: str) -> None:
            display_name = _tool_display_name(tool_name)
            tool_step = cl.Step(
                name=display_name,
                type="tool",
                parent_id=phase_step.id,
                show_input="json",
            )
            tool_step.input = args_summary or "(no args)"
            await tool_step.send()
            tool_steps[tool_name] = tool_step

        async def on_tool_end(tool_name: str, status: str, duration_ms: float | None) -> None:
            tool_step = tool_steps.pop(tool_name, None)
            if tool_step is not None:
                dur = f" ({duration_ms:.0f}ms)" if duration_ms else ""
                tool_step.output = f"{status}{dur}"
                await tool_step.update()

        # Pass UI callbacks through to the step function -> agent.run()
        merged_kwargs = {**coro_kwargs, "on_tool_start": on_tool_start, "on_tool_end": on_tool_end}
        result = await coro_fn(*coro_args, **merged_kwargs)

        # Show summary in the phase step output
        if isinstance(result, dict):
            agent_run = result.get("agent_run", {})
            num_tools = len(agent_run.get("tool_calls", []))
            is_fallback = agent_run.get("is_fallback", False)
            cost = agent_run.get("total_cost_usd")

            parts = []
            if is_fallback:
                parts.append("deterministic fallback (no LLM)")
            else:
                parts.append(f"{num_tools} tool calls")
                if cost:
                    parts.append(f"${float(cost):.4f}")
            phase_step.output = " | ".join(parts)
        else:
            phase_step.output = "completed"

        return result


async def _answer_follow_up(ticker: str, question: str, conversation_id: str | None) -> str:
    from src.pipeline import answer_question
    return await answer_question(ticker, question, conversation_id=conversation_id)


# ---------------------------------------------------------------------------
# Chat handlers
# ---------------------------------------------------------------------------

if cl is not None:
    @cl.on_chat_start
    async def on_chat_start() -> None:
        supported = ", ".join(list_supported_tickers())
        await cl.Message(
            content=(
                "Welcome to **Investment Research**.\n\n"
                "**Type a ticker to start:**\n"
                f"  Available: `{supported}`\n\n"
                "If reports already exist, they load instantly (no LLM cost).\n"
                "Otherwise the UI runs **Phase 1 → Phase 2 → Phase 3** on the **ingested corpus** "
                "already on disk (`data/processed/...`). That is **not** the same as fetch/ingest: "
                "those are optional **data-engineering** steps you run when you need to populate or "
                "refresh raw files and the vector index.\n\n"
                f"{_welcome_mcp_block()}\n"
                "After reports are displayed, ask follow-up questions in the same chat."
            )
        ).send()

    @cl.on_message
    async def on_message(message: "cl.Message") -> None:
        text = message.content.strip()

        supported = {t.upper() for t in list_supported_tickers()}
        active_ticker = cl.user_session.get("ticker")

        # ── Start research for a ticker ───────────────────────────────
        if text.upper() in supported:
            ticker = text.upper()
            conversation_id = _new_conversation_id()
            cl.user_session.set("ticker", ticker)
            cl.user_session.set("conversation_id", conversation_id)

            # Try cached reports first (instant, no LLM cost)
            cached = _load_cached_reports(ticker)
            if cached is not None:
                async with cl.Step(name="Loading Cached Reports", type="tool") as step:
                    step.output = f"Found existing reports for {ticker} — no pipeline needed"
                result = cached
            else:
                result = await _run_pipeline_with_steps(ticker)

            if isinstance(result, dict) and result.get("corpus_error"):
                await cl.Message(content=result["corpus_error"]).send()
                return

            # Build the final response from phase outputs
            phase1 = result.get("phase1_result", {}) if isinstance(result, dict) else {}
            phase2 = result.get("phase2_result", {}) if isinstance(result, dict) else {}
            phase3 = result.get("phase3_result", {}) if isinstance(result, dict) else {}

            parts = []
            if phase1.get("markdown"):
                parts.append(phase1["markdown"])
            if phase2.get("markdown"):
                parts.append(phase2["markdown"])
            if phase3.get("markdown"):
                parts.append(phase3["markdown"])

            if result.get("cached"):
                parts.append("---\n*Loaded from cached reports. Type `rerun SOC US` to regenerate.*")

            # Run summary (if pipeline ran)
            summary = result.get("summary", {}) if isinstance(result, dict) else {}
            if isinstance(summary, dict) and (summary.get("verdict") or summary.get("total_cost_usd")):
                lines = ["### Run Summary"]
                if summary.get("verdict"):
                    lines.append(f"- **Verdict:** {summary['verdict']}")
                cost = float(summary.get("total_cost_usd") or 0)
                if cost > 0:
                    lines.append(f"- **Claude cost:** ${cost:.4f}")
                parts.append("\n".join(lines))

            output = "\n\n---\n\n".join(parts) if parts else str(result)
            await cl.Message(content=output).send()
            return

        # ── Force rerun ───────────────────────────────────────────────
        if text.lower().startswith("rerun "):
            rerun_ticker = text[6:].strip().upper()
            if rerun_ticker in supported:
                cl.user_session.set("ticker", rerun_ticker)
                cl.user_session.set("conversation_id", _new_conversation_id())

                result = await _run_pipeline_with_steps(rerun_ticker)

                if isinstance(result, dict) and result.get("corpus_error"):
                    await cl.Message(content=result["corpus_error"]).send()
                    return

                phase1 = result.get("phase1_result", {}) if isinstance(result, dict) else {}
                phase2 = result.get("phase2_result", {}) if isinstance(result, dict) else {}
                phase3 = result.get("phase3_result", {}) if isinstance(result, dict) else {}

                parts = []
                if phase1.get("markdown"):
                    parts.append(phase1["markdown"])
                if phase2.get("markdown"):
                    parts.append(phase2["markdown"])
                if phase3.get("markdown"):
                    parts.append(phase3["markdown"])

                output = "\n\n---\n\n".join(parts) if parts else str(result)
                await cl.Message(content=output).send()
                return

        # ── Follow-up question ────────────────────────────────────────
        if active_ticker:
            conversation_id = cl.user_session.get("conversation_id")
            async with cl.Step(name="followup-analyst | Answering Question", type="run") as step:
                step.input = {"question": text, "ticker": active_ticker}
                answer = await _answer_follow_up(active_ticker, text, conversation_id)
                step.output = "answered"
            await cl.Message(content=answer).send()
            return

        # ── Unknown input ─────────────────────────────────────────────
        await cl.Message(
            content=(
                f"I don't recognize that ticker. Available companies: `{', '.join(list_supported_tickers())}`"
            )
        ).send()
