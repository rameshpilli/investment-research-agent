"""
Centralized agent definitions for the investment research pipeline.

This module provides two things:

1. ``AGENT_REGISTRY`` -- A dict of ``AgentDefinition`` objects keyed by phase name.
   These are passed to ``ClaudeAgentOptions.agents`` so the SDK natively knows
   about every research agent.

2. ``get_agent_config()`` -- Returns the per-phase run configuration (system prompt,
   effort, budget, max turns, model) used by ``ResearchAgent`` when executing
   each phase.

The Claude Code agent markdown files live in ``.claude/agents/`` and mirror
these definitions for interactive use.
"""

from src.agents.definitions import (
    AGENT_REGISTRY,
    AgentPhaseConfig,
    get_agent_config,
)

__all__ = ["AGENT_REGISTRY", "AgentPhaseConfig", "get_agent_config"]
