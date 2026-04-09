"""
Claude research agent built on the Claude Agent SDK.

This module provides two execution modes driven by ``AgentPhaseConfig``:

1. **One-shot** runs via ``query()`` -- used for Phase 1, Phase 3, and follow-up
2. **Stateful** multi-turn runs via ``ClaudeSDKClient`` -- used for Phase 2 (dossier)
   where deeper adversarial reasoning benefits from multiple tool-call rounds

Both modes share the same MCP tool catalog, hook-based tracking, project-level
Claude settings, and optional structured outputs. The agent can bind either to
an external Streamable HTTP MCP server or to the local in-process SDK tool
adapter, depending on configuration.

Agent configuration is centralized in ``src.agents.definitions``
rather than scattered across step files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from src.agents.definitions import AgentPhaseConfig, get_agent_config
from src.config import settings
from src.services.mcp_server import TOOLS_ANTHROPIC

logger = logging.getLogger(__name__)

# When a phase exhausts turns during tool use, the SDK can stop with
# stop_reason=tool_use and no structured_output. One follow-up user turn (no tools)
# reuses the same session context to emit the required JSON schema.
_STRUCTURED_OUTPUT_CONTINUATION_PROMPTS: dict[str, str] = {
    "gap-analyst": """\
You stopped before returning the required structured JSON ingestion report (often after many tool calls).

Do not call any more tools. Synthesize the Phase 1 ingestion report using only evidence already retrieved in this conversation.

Return ONLY the structured JSON matching the configured output schema (source_coverage, corpus_freshness, quality_assessment, missing_context). Every factual claim must include citations tied to documents you already inspected.
""",
    "adversarial-researcher": """\
You stopped before returning the required structured JSON dossier (often after many tool calls).

Do not call any more tools. Synthesize the Phase 2 adversarial dossier using only evidence already retrieved in this conversation.

Return ONLY the structured JSON matching the configured output schema (executive_verdict, thesis_summary, company_overview, valuation_context, top_nonconsensus_risks, contradictions_tone_shifts, what_would_change_verdict, information_gaps). Every factual claim must include citations tied to documents you already inspected.
""",
    "briefing-writer": """\
You stopped before returning the required structured JSON analyst brief (often after many tool calls).

Do not call any more tools. Synthesize the Phase 3 analyst brief using only evidence already retrieved in this conversation.

Return ONLY the structured JSON matching the configured output schema (thesis_summary, key_findings, top_risks, critical_gaps, recommended_next_steps). Every factual claim must include citations tied to documents you already inspected.
""",
}

_DEFAULT_CONTINUATION_PROMPT = """\
You stopped before returning the required structured JSON output (often after many tool calls).

Do not call any more tools. Synthesize the report using only evidence already retrieved in this conversation.

Return ONLY the structured JSON matching the configured output schema. Every factual claim must include citations tied to documents you already inspected.
"""

# Callback type for UI tool-call notifications.
# on_tool_start(tool_name, tool_input_summary) -> None
# on_tool_end(tool_name, status, duration_ms) -> None
ToolStartCallback = Any  # async (str, str) -> None
ToolEndCallback = Any  # async (str, str, float | None) -> None


@dataclass
class AgentRunResult:
    """Normalized result from a Claude Agent SDK run."""

    text: str
    structured_output: Any = None
    session_id: str | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    model_usage: dict[str, Any] | None = None
    stop_reason: str | None = None
    num_turns: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    context_usage: dict[str, Any] | None = None
    is_fallback: bool = False
    error: str | None = None


@dataclass
class _ToolHookState:
    calls: list[dict[str, Any]] = field(default_factory=list)
    _call_index: dict[str, int] = field(default_factory=dict)
    _start_times: dict[str, float] = field(default_factory=dict)

    def start(self, tool_use_id: str, tool_name: str, tool_input: dict[str, Any]) -> None:
        self._start_times[tool_use_id] = time.perf_counter()
        self._call_index[tool_use_id] = len(self.calls)
        self.calls.append(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_input_summary": _summarize_args(tool_input),
                "status": "started",
            }
        )

    def finish(self, tool_use_id: str, *, tool_response: Any = None, error: str | None = None) -> None:
        index = self._call_index.get(tool_use_id)
        if index is None:
            return
        started = self._start_times.pop(tool_use_id, None)
        duration_ms = round((time.perf_counter() - started) * 1000, 2) if started is not None else None
        record = self.calls[index]
        record["duration_ms"] = duration_ms
        if error:
            record["status"] = "error"
            record["error"] = error
            return
        record["status"] = "ok"
        if tool_response is not None:
            try:
                record["response_size_chars"] = len(json.dumps(tool_response, default=str))
            except TypeError:
                record["response_size_chars"] = len(str(tool_response))


@dataclass
class _FollowUpSession:
    client: Any
    tool_server: Any
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_hooks: _ToolHookState | None = None


class ResearchAgent:
    """Claude research agent using the Claude Agent SDK.

    Each run is configured by an ``AgentPhaseConfig`` from the centralized
    agent registry (``src.agents.definitions``).  The step
    files pass the phase name and this class looks up the right config.
    """

    def __init__(self, prompts_dir: Path | None = None, use_llm: bool | None = None) -> None:
        self.prompts_dir = prompts_dir or settings.prompts_dir
        self.use_llm = settings.use_llm if use_llm is None else use_llm
        self._followup_sessions: dict[str, _FollowUpSession] = {}
        self._followup_session_lock = asyncio.Lock()

    def _load_prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.txt"
        return path.read_text(encoding="utf-8")

    def _sdk_available(self) -> bool:
        return self.use_llm and bool(os.getenv("ANTHROPIC_API_KEY"))

    def _llm_unavailable_reason(self) -> str:
        if not self.use_llm:
            return "LLM is disabled (RESEARCH_USE_LLM=false)."
        if not os.getenv("ANTHROPIC_API_KEY"):
            return "ANTHROPIC_API_KEY is not set."
        return "Claude SDK is unavailable."

    def _fallback_with_reason(self, fallback_markdown: str) -> AgentRunResult:
        reason = self._llm_unavailable_reason()
        note = (
            "> Note: Claude-powered mode is unavailable. "
            f"{reason} Running deterministic fallback output.\n\n"
        )
        return AgentRunResult(
            text=f"{note}{fallback_markdown}",
            is_fallback=True,
            error=reason,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        phase: str,
        variables: dict[str, Any],
        tool_server: Any,
        fallback_markdown: str,
        *,
        output_schema: Any | None = None,
        config_override: AgentPhaseConfig | None = None,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> AgentRunResult:
        """Run a research phase.

        Looks up the ``AgentPhaseConfig`` for *phase* and dispatches to
        either ``_run_oneshot`` or ``_run_stateful`` depending on the
        config's ``use_stateful_client`` flag.

        Optional *on_tool_start* and *on_tool_end* callbacks are invoked
        for every tool call so UI layers (e.g. Chainlit) can show
        real-time progress.
        """
        if not self._sdk_available():
            return self._fallback_with_reason(fallback_markdown)

        config = config_override or get_agent_config(phase)
        template = self._load_prompt(config.prompt_name)
        user_prompt = template.format(**variables)

        try:
            if config.use_stateful_client:
                return await self._run_stateful(
                    config=config,
                    user_prompt=user_prompt,
                    tool_server=tool_server,
                    fallback_markdown=fallback_markdown,
                    output_schema=output_schema,
                    on_tool_start=on_tool_start,
                    on_tool_end=on_tool_end,
                )
            return await self._run_oneshot(
                config=config,
                user_prompt=user_prompt,
                tool_server=tool_server,
                fallback_markdown=fallback_markdown,
                output_schema=output_schema,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
        except Exception as exc:
            # Phase 1 often needs many tool rounds + structured output; transient CLI
            # failures (exit code 1) or turn pressure recover well with one retry.
            if phase == "gap_analysis" and not config.use_stateful_client:
                logger.warning(
                    "Agent [%s] oneshot failed (%s); retrying once after short delay",
                    config.name,
                    exc,
                )
                await asyncio.sleep(2)
                try:
                    return await self._run_oneshot(
                        config=config,
                        user_prompt=user_prompt,
                        tool_server=tool_server,
                        fallback_markdown=fallback_markdown,
                        output_schema=output_schema,
                        on_tool_start=on_tool_start,
                        on_tool_end=on_tool_end,
                    )
                except Exception as exc2:
                    logger.error(
                        "Agent SDK loop failed [%s] after retry: %s",
                        config.name,
                        exc2,
                        exc_info=True,
                    )
                    return AgentRunResult(
                        text=fallback_markdown, is_fallback=True, error=str(exc2)
                    )
            logger.error("Agent SDK loop failed [%s]: %s", config.name, exc, exc_info=True)
            return AgentRunResult(text=fallback_markdown, is_fallback=True, error=str(exc))

    async def run_followup(
        self,
        variables: dict[str, Any],
        tool_server: Any,
        fallback_markdown: str,
        *,
        conversation_id: str | None,
        output_schema: Any | None = None,
    ) -> AgentRunResult:
        """Run a follow-up query, reusing ClaudeSDKClient sessions when possible."""
        config = get_agent_config("followup")

        if not conversation_id:
            return await self.run(
                phase="followup",
                variables=variables,
                tool_server=tool_server,
                fallback_markdown=fallback_markdown,
                output_schema=output_schema,
            )

        if not self._sdk_available():
            return self._fallback_with_reason(fallback_markdown)

        session_key = f"{conversation_id}:{tool_server.profile.ticker}:{config.prompt_name}"
        template = self._load_prompt(config.prompt_name)
        user_prompt = template.format(**variables)

        try:
            session = await self._get_or_create_followup_session(
                session_key=session_key,
                config=config,
                tool_server=tool_server,
                output_schema=output_schema,
            )
            async with session.lock:
                session.tool_server.invalidate_cache()
                session.active_hooks = _ToolHookState()
                await session.client.query(user_prompt, session_id=session.session_id)

                from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

                text_fragments: list[str] = []
                result_message: ResultMessage | None = None
                async for message in session.client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                text_fragments.append(block.text)
                    elif isinstance(message, ResultMessage):
                        result_message = message

                context_usage = await session.client.get_context_usage()
                return self._finalize_result(
                    agent_name=config.name,
                    fallback_markdown=fallback_markdown,
                    text_fragments=text_fragments,
                    result_message=result_message,
                    hook_state=session.active_hooks,
                    context_usage=context_usage,
                )
        except Exception as exc:
            logger.error("Follow-up SDK session failed [%s]: %s", session_key, exc, exc_info=True)
            await self._drop_followup_session(session_key)
            return AgentRunResult(text=fallback_markdown, is_fallback=True, error=str(exc))
        finally:
            if "session" in locals():
                session.active_hooks = None

    # ------------------------------------------------------------------
    # One-shot execution (Phase 1, Phase 3, follow-up without session)
    # ------------------------------------------------------------------

    async def _run_oneshot(
        self,
        *,
        config: AgentPhaseConfig,
        user_prompt: str,
        tool_server: Any,
        fallback_markdown: str,
        output_schema: Any | None,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> AgentRunResult:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolAnnotations,
            create_sdk_mcp_server,
            query,
            tool as sdk_tool,
        )

        hook_state = _ToolHookState()
        mcp_server = _build_mcp_server_config(tool_server, sdk_tool=sdk_tool, tool_annotations_cls=ToolAnnotations, create_sdk_mcp_server_fn=create_sdk_mcp_server)
        options = self._build_options(
            hook_matcher_cls=HookMatcher,
            mcp_server=mcp_server,
            tool_names=_allowed_tool_names(),
            config=config,
            output_schema=output_schema,
            hook_state_getter=lambda: hook_state,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )

        text_fragments: list[str] = []
        result_message: ResultMessage | None = None
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_fragments.append(block.text)
            elif isinstance(message, ResultMessage):
                result_message = message

        return self._finalize_result(
            agent_name=config.name,
            fallback_markdown=fallback_markdown,
            text_fragments=text_fragments,
            result_message=result_message,
            hook_state=hook_state,
        )

    # ------------------------------------------------------------------
    # Stateful execution (Phase 2 -- multi-turn adversarial research)
    # ------------------------------------------------------------------

    async def _run_stateful(
        self,
        *,
        config: AgentPhaseConfig,
        user_prompt: str,
        tool_server: Any,
        fallback_markdown: str,
        output_schema: Any | None,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> AgentRunResult:
        """Run via ClaudeSDKClient for multi-turn reasoning.

        Phase 2 benefits from a stateful session because the adversarial
        research process involves multiple rounds of tool calls: searching
        by topic, comparing filings, iterating on contradictions, and
        synthesising across all evidence before writing the dossier.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKClient,
            HookMatcher,
            ResultMessage,
            TextBlock,
            ToolAnnotations,
            create_sdk_mcp_server,
            tool as sdk_tool,
        )

        hook_state = _ToolHookState()
        mcp_server = _build_mcp_server_config(tool_server, sdk_tool=sdk_tool, tool_annotations_cls=ToolAnnotations, create_sdk_mcp_server_fn=create_sdk_mcp_server)
        options = self._build_options(
            hook_matcher_cls=HookMatcher,
            mcp_server=mcp_server,
            tool_names=_allowed_tool_names(),
            config=config,
            output_schema=output_schema,
            hook_state_getter=lambda: hook_state,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )

        text_fragments: list[str] = []
        result_message: ResultMessage | None = None

        client = ClaudeSDKClient(options)
        try:
            await client.connect(prompt=user_prompt)

            async def drain_response() -> tuple[list[str], ResultMessage | None]:
                fragments: list[str] = []
                rm: ResultMessage | None = None
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                fragments.append(block.text)
                    elif isinstance(message, ResultMessage):
                        rm = message
                return fragments, rm

            text_fragments, result_message = await drain_response()

            if (
                output_schema is not None
                and result_message is not None
                and not result_message.is_error
                and getattr(result_message, "structured_output", None) is None
                and hook_state.calls
            ):
                logger.warning(
                    "Stateful run [%s] finished without structured output (stop=%s, tools=%d); "
                    "sending synthesis continuation",
                    config.name,
                    getattr(result_message, "stop_reason", None),
                    len(hook_state.calls),
                )
                sid = getattr(result_message, "session_id", None) or "default"
                continuation = _STRUCTURED_OUTPUT_CONTINUATION_PROMPTS.get(
                    config.name, _DEFAULT_CONTINUATION_PROMPT
                )
                await client.query(continuation, session_id=sid)
                text_more, result_more = await drain_response()
                if result_more and getattr(result_more, "structured_output", None) is not None:
                    c0 = float(result_message.total_cost_usd or 0)
                    c1 = float(result_more.total_cost_usd or 0)
                    merged_cost = c0 + c1 if (c0 or c1) else result_more.total_cost_usd
                    result_message = replace(result_more, total_cost_usd=merged_cost)
                    text_fragments = text_more
                elif result_more:
                    logger.warning(
                        "Continuation turn still produced no structured output (stop=%s)",
                        getattr(result_more, "stop_reason", None),
                    )

            context_usage = await client.get_context_usage()
            return self._finalize_result(
                agent_name=config.name,
                fallback_markdown=fallback_markdown,
                text_fragments=text_fragments,
                result_message=result_message,
                hook_state=hook_state,
                context_usage=context_usage,
            )
        finally:
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Ignoring stateful client disconnect error for %s", config.name, exc_info=True)

    # ------------------------------------------------------------------
    # Follow-up session management
    # ------------------------------------------------------------------

    async def _get_or_create_followup_session(
        self,
        *,
        session_key: str,
        config: AgentPhaseConfig,
        tool_server: Any,
        output_schema: Any | None,
    ) -> _FollowUpSession:
        async with self._followup_session_lock:
            existing = self._followup_sessions.get(session_key)
            if existing is not None:
                return existing

            from claude_agent_sdk import (
                ClaudeSDKClient,
                HookMatcher,
                ToolAnnotations,
                create_sdk_mcp_server,
                tool as sdk_tool,
            )

            session = _FollowUpSession(client=None, tool_server=tool_server, session_id=session_key)
            mcp_server = _build_mcp_server_config(tool_server, sdk_tool=sdk_tool, tool_annotations_cls=ToolAnnotations, create_sdk_mcp_server_fn=create_sdk_mcp_server)
            options = self._build_options(
                hook_matcher_cls=HookMatcher,
                mcp_server=mcp_server,
                tool_names=_allowed_tool_names(),
                config=config,
                output_schema=output_schema,
                hook_state_getter=lambda: session.active_hooks,
            )
            session.client = ClaudeSDKClient(options)
            await session.client.connect()
            self._followup_sessions[session_key] = session
            return session

    async def _drop_followup_session(self, session_key: str) -> None:
        async with self._followup_session_lock:
            session = self._followup_sessions.pop(session_key, None)
        if session is not None:
            try:
                await session.client.disconnect()
            except Exception:
                logger.debug("Ignoring follow-up client disconnect failure for %s", session_key, exc_info=True)

    # ------------------------------------------------------------------
    # Options builder
    # ------------------------------------------------------------------

    def _build_options(
        self,
        *,
        hook_matcher_cls: Any,
        mcp_server: Any,
        tool_names: list[str],
        config: AgentPhaseConfig,
        output_schema: Any | None,
        hook_state_getter: Any,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions
        from src.agents.definitions import AGENT_REGISTRY

        options = ClaudeAgentOptions(
            system_prompt=config.system_prompt,
            model=config.model or settings.anthropic_model,
            fallback_model=settings.anthropic_fallback_model,
            max_turns=config.max_turns,
            max_budget_usd=config.max_budget_usd,
            effort=config.effort,
            mcp_servers={"research-corpus": mcp_server},
            allowed_tools=tool_names,
            cwd=str(settings.repo_root),
            setting_sources=["project"],
            hooks=self._build_hook_config(hook_matcher_cls, hook_state_getter, on_tool_start, on_tool_end),
            agents=AGENT_REGISTRY or None,
        )
        if output_schema is not None:
            options.output_format = _output_format_for_schema(output_schema)
        return options

    def _build_hook_config(
        self,
        hook_matcher_cls: Any,
        hook_state_getter: Any,
        on_tool_start: ToolStartCallback | None = None,
        on_tool_end: ToolEndCallback | None = None,
    ) -> dict[str, list[Any]]:
        async def pre_tool_use(input_data: dict[str, Any], tool_use_id: str, context: dict[str, Any]) -> dict[str, Any]:
            state = hook_state_getter()
            if state is None:
                return {}
            tool_name = input_data.get("tool_name", "unknown")
            tool_input = input_data.get("tool_input", {})
            state.start(tool_use_id, tool_name, tool_input)
            logger.info("Tool [call %d]: %s(%s)", len(state.calls), tool_name, _summarize_args(tool_input))
            if on_tool_start is not None:
                try:
                    await on_tool_start(tool_name, _summarize_args(tool_input))
                except Exception:
                    logger.debug("on_tool_start callback error", exc_info=True)
            return {}

        async def post_tool_use(input_data: dict[str, Any], tool_use_id: str, context: dict[str, Any]) -> dict[str, Any]:
            state = hook_state_getter()
            if state is None:
                return {}
            state.finish(tool_use_id, tool_response=input_data.get("tool_response"))
            if on_tool_end is not None:
                idx = state._call_index.get(tool_use_id)
                record = state.calls[idx] if idx is not None else {}
                try:
                    await on_tool_end(record.get("tool_name", "unknown"), "ok", record.get("duration_ms"))
                except Exception:
                    logger.debug("on_tool_end callback error", exc_info=True)
            return {}

        async def post_tool_use_failure(input_data: dict[str, Any], tool_use_id: str, context: dict[str, Any]) -> dict[str, Any]:
            state = hook_state_getter()
            if state is None:
                return {}
            state.finish(tool_use_id, error=input_data.get("error", "unknown tool error"))
            if on_tool_end is not None:
                try:
                    await on_tool_end(input_data.get("tool_name", "unknown"), "error", None)
                except Exception:
                    logger.debug("on_tool_end callback error", exc_info=True)
            return {}

        async def stop_hook(input_data: dict[str, Any], tool_use_id: str, context: dict[str, Any]) -> dict[str, Any]:
            logger.debug("Claude stop hook triggered")
            return {}

        return {
            "PreToolUse": [hook_matcher_cls(matcher="*", hooks=[pre_tool_use])],
            "PostToolUse": [hook_matcher_cls(matcher="*", hooks=[post_tool_use])],
            "PostToolUseFailure": [hook_matcher_cls(matcher="*", hooks=[post_tool_use_failure])],
            "Stop": [hook_matcher_cls(matcher="*", hooks=[stop_hook])],
        }

    # ------------------------------------------------------------------
    # Result normalization
    # ------------------------------------------------------------------

    def _finalize_result(
        self,
        *,
        agent_name: str,
        fallback_markdown: str,
        text_fragments: list[str],
        result_message: Any | None,
        hook_state: _ToolHookState | None,
        context_usage: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        structured_output = getattr(result_message, "structured_output", None)

        # Prefer the final result text over concatenated thinking fragments.
        # In multi-turn (stateful) runs, text_fragments contains ALL intermediate
        # reasoning ("Let me search...", "I can see..."). The actual dossier/report
        # is in result_message.result or structured_output.
        if result_message is not None and getattr(result_message, "result", None):
            text = str(result_message.result).strip()
        else:
            text = "\n".join(text_fragments).strip()
        text = text or fallback_markdown

        result = AgentRunResult(
            text=text,
            structured_output=structured_output,
            session_id=getattr(result_message, "session_id", None),
            total_cost_usd=getattr(result_message, "total_cost_usd", None),
            usage=getattr(result_message, "usage", None),
            model_usage=getattr(result_message, "model_usage", None),
            stop_reason=getattr(result_message, "stop_reason", None),
            num_turns=getattr(result_message, "num_turns", None),
            tool_calls=list(hook_state.calls) if hook_state is not None else [],
            context_usage=context_usage,
        )
        logger.info(
            "Agent [%s] completed: %d tool calls, stop=%s, cost=%s, session=%s",
            agent_name,
            len(result.tool_calls),
            result.stop_reason,
            result.total_cost_usd,
            result.session_id,
        )
        return result


# ---------------------------------------------------------------------------
# Tool building helpers
# ---------------------------------------------------------------------------

def _build_sdk_tools(tool_server: Any, sdk_tool: Any, tool_annotations_cls: Any) -> list[Any]:
    """Build @tool-decorated SDK tools from the shared tool catalog."""
    tools: list[Any] = []
    annotations = tool_annotations_cls(
        readOnlyHint=True,
        idempotentHint=True,
        destructiveHint=False,
        openWorldHint=False,
    )
    for tool_definition in TOOLS_ANTHROPIC:
        tool_name = tool_definition["name"]
        description = tool_definition["description"]
        arg_types = _sdk_argument_types(tool_definition["input_schema"])

        async def _dispatch(args: dict[str, Any], *, _tool_name: str = tool_name) -> dict[str, Any]:
            result = tool_server.call(_tool_name, args)
            try:
                payload = json.loads(result)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return {"content": [{"type": "text", "text": result}]}

        _dispatch.__name__ = tool_name
        tools.append(sdk_tool(tool_name, description, arg_types, annotations=annotations)(_dispatch))
    return tools


def _allowed_tool_names() -> list[str]:
    return [f"mcp__research-corpus__{tool['name']}" for tool in TOOLS_ANTHROPIC]


def _build_mcp_server_config(
    tool_server: Any,
    *,
    sdk_tool: Any,
    tool_annotations_cls: Any,
    create_sdk_mcp_server_fn: Any,
) -> dict[str, Any]:
    if settings.mcp_url:
        return {
            "type": "http",
            "url": settings.mcp_url,
            "headers": {"X-Research-Ticker": tool_server.profile.ticker},
        }

    sdk_tools = _build_sdk_tools(tool_server, sdk_tool, tool_annotations_cls)
    return create_sdk_mcp_server_fn(name="research-corpus", version="1.1.0", tools=sdk_tools)


def _sdk_argument_types(input_schema: dict[str, Any]) -> dict[str, type]:
    """Translate shared JSON schema into the Python type map the SDK expects."""
    json_to_python: dict[str, type] = {
        "boolean": bool,
        "integer": int,
        "number": float,
        "string": str,
    }
    properties = input_schema.get("properties", {})
    return {name: json_to_python.get(spec.get("type", "string"), str) for name, spec in properties.items()}


def _output_format_for_schema(output_schema: Any) -> dict[str, Any]:
    if hasattr(output_schema, "model_json_schema"):
        schema = output_schema.model_json_schema()
    elif isinstance(output_schema, dict):
        schema = output_schema
    else:
        raise TypeError(f"Unsupported output schema: {type(output_schema)!r}")
    return {"type": "json_schema", "schema": schema}


def _summarize_args(args: dict[str, Any]) -> str:
    """Short summary of tool args for logging."""
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        text = str(value)
        if len(text) > 60:
            text = text[:60] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)
