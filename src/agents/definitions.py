"""
Agent definitions for each research phase.

Each phase gets:
- An ``AgentDefinition`` (SDK-native, registered on ``ClaudeAgentOptions.agents``)
- An ``AgentPhaseConfig`` (runtime settings: system prompt, effort, budget, model)

The ``AgentDefinition`` objects tell the SDK what sub-agents exist and how they
behave.  The ``AgentPhaseConfig`` objects carry the per-run parameters that
``ResearchAgent`` uses when it calls ``query()`` or ``ClaudeSDKClient``.

Adding a new agent = adding one entry to ``_PHASE_CONFIGS`` below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from src.config import settings


def _phase1_max_turns() -> int:
    """Phase 1 (gap analysis) runs many filing-section reads then structured JSON.

    Default: max(3 × general tool rounds, 35). Override with RESEARCH_PHASE1_MAX_TURNS.
    (Using only RESEARCH_LLM_MAX_TOOL_ROUNDS caused subprocess exit once tool+schema
    steps exceeded the turn ceiling.)
    """
    raw = os.getenv("RESEARCH_PHASE1_MAX_TURNS")
    if raw:
        return int(raw)
    return max(settings.llm_max_tool_rounds * 3, 35)


def _phase2_max_turns() -> int:
    """Phase 2 (dossier) needs a higher ceiling than Phase 1: each tool call consumes turns.

    Default: max(3 × general tool rounds, 40). Override with RESEARCH_PHASE2_MAX_TURNS.
    """
    raw = os.getenv("RESEARCH_PHASE2_MAX_TURNS")
    if raw:
        return int(raw)
    return max(settings.llm_max_tool_rounds * 3, 40)


# ---------------------------------------------------------------------------
# Per-phase runtime config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentPhaseConfig:
    """All settings needed to run a single research phase."""

    name: str
    prompt_name: str
    system_prompt: str
    description: str
    effort: str
    max_budget_usd: float
    max_turns: int
    model: str | None = None
    use_stateful_client: bool = False


_PHASE_CONFIGS: dict[str, AgentPhaseConfig] = {
    "gap_analysis": AgentPhaseConfig(
        name="gap-analyst",
        prompt_name="gap_analysis",
        system_prompt=(
            "You are a skeptical investment research librarian. "
            "Your job is to assess what documents are available and what is missing. "
            "Be factual and concise. Cite every claim."
        ),
        description=(
            "Phase 1 ingestion quality and coverage analyst. "
            "Assesses corpus completeness, freshness, and identifies missing materials."
        ),
        effort="low",
        max_budget_usd=settings.phase1_max_budget_usd,
        max_turns=_phase1_max_turns(),
    ),
    "dossier": AgentPhaseConfig(
        name="adversarial-researcher",
        prompt_name="dossier",
        system_prompt=(
            "You are an adversarial investment analyst. Your job is to stress-test "
            "the thesis, not summarize management's view. Use the tools to search "
            "the corpus, compare filings, and find what doesn't add up. Cite every "
            "claim. Flag uncertainty. Be decisive in your verdict."
        ),
        description=(
            "Phase 2 deep research agent. Produces adversarial investment dossiers "
            "by stress-testing theses, detecting contradictions and tone shifts."
        ),
        effort="high",
        max_budget_usd=settings.phase2_max_budget_usd,
        max_turns=_phase2_max_turns(),
        use_stateful_client=True,
    ),
    "briefing": AgentPhaseConfig(
        name="briefing-writer",
        prompt_name="briefing",
        system_prompt=(
            "You are writing a one-page analyst brief. Be tight and "
            "decision-oriented. Cite every claim. Lead with the answer."
        ),
        description=(
            "Phase 3 analyst briefing synthesizer. Condenses Phase 1 and Phase 2 "
            "outputs into a one-page decision-oriented brief."
        ),
        effort="low",
        max_budget_usd=settings.phase3_max_budget_usd,
        max_turns=settings.llm_max_tool_rounds,
    ),
    "followup": AgentPhaseConfig(
        name="followup-analyst",
        prompt_name="followup",
        system_prompt=(
            "Answer only from the supplied evidence. Do not add outside knowledge. "
            "Cite every claim. If the evidence does not support an answer, say so."
        ),
        description=(
            "Follow-up Q&A analyst. Answers analyst questions grounded exclusively "
            "in the ingested corpus."
        ),
        effort="low",
        max_budget_usd=settings.followup_max_budget_usd,
        max_turns=settings.llm_max_tool_rounds,
    ),
}


def get_agent_config(phase: str) -> AgentPhaseConfig:
    """Return the agent configuration for a pipeline phase.

    Raises ``KeyError`` if the phase is unknown.
    """
    config = _PHASE_CONFIGS.get(phase)
    if config is None:
        raise KeyError(f"Unknown agent phase '{phase}'. Available: {list(_PHASE_CONFIGS)}")
    return config


# ---------------------------------------------------------------------------
# SDK AgentDefinition registry
# ---------------------------------------------------------------------------

def _build_agent_registry() -> dict[str, Any]:
    """Build a dict of SDK ``AgentDefinition`` objects for all phases.

    These are passed to ``ClaudeAgentOptions(agents=...)`` so the SDK
    natively registers each research agent as a delegatable sub-agent.

    Import is deferred so the module loads even without the SDK installed
    (e.g. during tests with ``RESEARCH_USE_LLM=false``).
    """
    try:
        from claude_agent_sdk import AgentDefinition
    except ImportError:
        return {}

    tool_names = [
        "mcp__research-corpus__list_available_companies",
        "mcp__research-corpus__search_corpus",
        "mcp__research-corpus__list_documents",
        "mcp__research-corpus__get_document",
        "mcp__research-corpus__get_filing_section",
        "mcp__research-corpus__compare_filings",
        "mcp__research-corpus__get_missing_materials",
        "mcp__research-corpus__get_corpus_status",
    ]

    registry: dict[str, Any] = {}
    for phase, config in _PHASE_CONFIGS.items():
        registry[config.name] = AgentDefinition(
            description=config.description,
            prompt=config.system_prompt,
            tools=tool_names,
            model=config.model or settings.anthropic_model,
            maxTurns=config.max_turns,
            effort=config.effort,
        )
    return registry


AGENT_REGISTRY: dict[str, Any] = _build_agent_registry()
