#!/usr/bin/env python3
"""Step 2 Test: agent service — fallback mode, prompt loading, and tool integration."""

import asyncio
import json
from tempfile import TemporaryDirectory
from src.tests.helpers import section, check, show, report, build_soc_docs, populate_store
from src.models import get_company
from src.config import settings
from src.services.agent import ResearchAgent, _build_mcp_server_config
from src.services.mcp_server import TOOLS_ANTHROPIC, ResearchToolServer


def main():
    section("Agent — Fallback Mode")
    agent = ResearchAgent(use_llm=False)
    check("use_llm is False", agent.use_llm is False)
    check("SDK availability is disabled", agent._sdk_available() is False)

    fb = "This is the fallback output."
    soc = get_company("SOC US")
    with TemporaryDirectory() as tmp:
        store, _ = populate_store(tmp, soc, build_soc_docs(soc))
        tool_server = ResearchToolServer(soc, store)

        result = asyncio.run(agent.run(
            phase="gap_analysis",
            variables={"company_name": "Test", "ticker": "TST"},
            tool_server=tool_server,
            fallback_markdown=fb,
        ))
        check("run returns fallback payload when no LLM", fb in result.text)
        check("fallback note includes reason", "LLM is disabled" in result.text)
        check("fallback flag is set", result.is_fallback is True)

    section("Agent — Prompt Loading")
    for name in ["gap_analysis", "dossier", "briefing", "followup"]:
        tmpl = agent._load_prompt(name)
        check(f"Prompt '{name}' loads ({len(tmpl)} chars)", len(tmpl) > 50)
        check(f"Prompt '{name}' has placeholders", "{" in tmpl)
        show(f"  First 80 chars", tmpl[:80])

    section("Agent — Tool Server Integration")
    soc = get_company("SOC US")
    with TemporaryDirectory() as tmp:
        store, _ = populate_store(tmp, soc, build_soc_docs(soc))
        server = ResearchToolServer(soc, store)

        # Verify tool server works independently of agent
        r1 = json.loads(server.call("list_documents", {}))
        check(f"Tool server lists {len(r1)} docs", len(r1) > 0)

        r2 = json.loads(server.call("search_corpus", {"query": "revenue"}))
        check(f"Tool server searches: {len(r2)} results", len(r2) > 0)

        r3 = json.loads(server.call("get_missing_materials", {}))
        check(f"Tool server gaps: {len(r3)} materials", len(r3) > 0)

        show("Tool server is ready — Claude would call these tools with an API key", "")

    section("Agent — MCP Transport Selection")
    original_mcp_url = settings.mcp_url
    try:
        object.__setattr__(settings, "mcp_url", "http://localhost:8081/mcp")

        with TemporaryDirectory() as tmp:
            store, _ = populate_store(tmp, soc, build_soc_docs(soc))
            server = ResearchToolServer(soc, store)
            transport = _build_mcp_server_config(
                server,
                sdk_tool=None,
                tool_annotations_cls=None,
                create_sdk_mcp_server_fn=lambda **kwargs: {"unexpected": kwargs},
            )
            check("External MCP transport selected when RESEARCH_MCP_URL is set", transport.get("type") == "http")
            check("MCP URL is forwarded", transport.get("url") == "http://localhost:8081/mcp")
            check("Ticker header is attached", transport.get("headers", {}).get("X-Research-Ticker") == "SOC US")
    finally:
        object.__setattr__(settings, "mcp_url", original_mcp_url)

    section("Agent — Shared Tool Definitions")
    check(f"Tools defined: {len(TOOLS_ANTHROPIC)}", len(TOOLS_ANTHROPIC) > 0)
    for tool in TOOLS_ANTHROPIC:
        check(f"Tool '{tool['name']}' has input_schema", "input_schema" in tool)
        check(f"Tool '{tool['name']}' has description", bool(tool.get("description")))

    section("Agent — Prompt Variables Match")
    for name, variables in [
        ("gap_analysis", {"company_name": "Test", "ticker": "TST"}),
        ("dossier", {"company_name": "Test", "ticker": "TST"}),
        ("followup", {"company_name": "Test", "ticker": "TST", "question": "test?"}),
    ]:
        tmpl = agent._load_prompt(name)
        try:
            tmpl.format(**variables)
            check(f"Prompt '{name}' formats without error", True)
        except KeyError as e:
            check(f"Prompt '{name}' formats without error", False, f"Missing key: {e}")

    report()

if __name__ == "__main__":
    main()
