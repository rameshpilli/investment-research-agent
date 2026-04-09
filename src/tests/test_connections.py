#!/usr/bin/env python3
"""
Test: Live connections — verify the configured Claude API and embedding setup is reachable.
Run this FIRST to confirm your .env is configured correctly before running the pipeline.

    uv run python -m src.tests.test_connections
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# Load .env before anything else
from dotenv import load_dotenv
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH, override=True)

_pass = 0
_fail = 0


def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def check(label: str, ok: bool, detail: str = "") -> None:
    global _pass, _fail
    if ok:
        _pass += 1
        print(f"  \u2713 {label}")
    else:
        _fail += 1
        print(f"  \u2717 {label}")
        if detail:
            print(f"    \u2192 {detail}")


def show(label: str, value, max_lines: int = 8) -> None:
    text = str(value)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"  ... ({len(lines) - max_lines} more lines)"]
    indented = "\n".join(f"    {l}" for l in lines)
    print(f"  {label}:\n{indented}")


async def async_main():
    print("\n" + "="*70)
    print("  CONNECTION TESTS — CLAUDE API + EMBEDDING VERIFICATION")
    print("="*70)
    print(f"  Loaded .env from: {ENV_PATH}")

    from src.config import settings

    # ── 1. Environment check ─────────────────────────────────────────────
    section("1. Environment Variables")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    embed_key = os.getenv("RESEARCH_EMBEDDING_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    embed_provider = settings.embedding_provider
    embed_model = settings.embedding_model
    use_llm = settings.use_llm

    print(f"  ANTHROPIC_API_KEY:  {'set (' + anthropic_key[:12] + '...)' if len(anthropic_key) > 12 else 'NOT SET'}")
    print(f"  ANTHROPIC_MODEL:    {settings.anthropic_model}")
    print(f"  EMBEDDING_PROVIDER: {embed_provider}")
    print(f"  EMBEDDING_MODEL:    {embed_model}")
    print(f"  USE_LLM:            {use_llm}")

    llm_mode = "Claude enabled" if use_llm else "LLM disabled (deterministic fallback)"
    if embed_provider == "local":
        embedding_mode = "local hash embeddings (no API key needed)"
    elif embed_provider == "voyage":
        embedding_mode = "voyage (deprecated in config — EmbeddingService uses local)"
    else:
        embedding_mode = f"{embed_provider} embeddings"

    print(f"  EFFECTIVE_LLM_MODE: {llm_mode}")
    print(f"  EFFECTIVE_EMBEDDING_MODE: {embedding_mode}")

    if anthropic_key and not use_llm:
        show("Note", "ANTHROPIC_API_KEY is set, but RESEARCH_USE_LLM=false so Claude calls are skipped.")

    if use_llm:
        check("Anthropic API key is set", bool(anthropic_key), "Set ANTHROPIC_API_KEY when RESEARCH_USE_LLM=true")
    else:
        check("LLM disabled by config", True)

    if embed_provider == "local":
        check("Embedding provider is local (no API key needed)", True)
    elif embed_provider == "voyage":
        check(
            "Voyage provider removed (config still says voyage; EmbeddingService uses local)",
            True,
        )
    elif embed_provider in {"openai", "openai_compatible"}:
        check(
            "Embedding API key is set",
            bool(embed_key or openai_key),
            "Set RESEARCH_EMBEDDING_API_KEY or OPENAI_API_KEY for real embeddings",
        )
    else:
        check("Embedding provider is supported", False, f"Unknown provider: {embed_provider}")

    # ── 2. Embedding API ─────────────────────────────────────────────────
    section("2. Embedding Layer")

    from src.services.embeddings import EmbeddingService

    try:
        svc = EmbeddingService()
        print(f"  Provider: {svc.provider}")
        print(f"  Model:    {svc.model}")
        print(f"  Dims:     {svc.dimensions}")

        print("\n  Embedding a single query...")
        vec = svc.embed_query("offshore oil production revenue risk factors")
        check(f"Query embedded -> {len(vec)} dimensions", len(vec) > 100)
        show("First 10 values", vec[:10])

        print("\n  Batch embedding 3 texts...")
        vecs = svc.embed_texts([
            "Revenue was $150 million for fiscal year 2024",
            "Pipeline permits received from PHMSA",
            "Risk factors include environmental liability",
        ])
        check(f"Batch returned {len(vecs)} vectors", len(vecs) == 3)
        for i, v in enumerate(vecs):
            print(f"    Text {i+1}: {len(v)} dims, first 5 values: {v[:5]}")

        # Similarity test
        import math
        def cosine(a, b):
            dot = sum(x*y for x,y in zip(a,b))
            na = math.sqrt(sum(x*x for x in a))
            nb = math.sqrt(sum(x*x for x in b))
            return dot/(na*nb) if na and nb else 0

        sim_01 = cosine(vecs[0], vecs[1])
        sim_02 = cosine(vecs[0], vecs[2])
        sim_12 = cosine(vecs[1], vecs[2])
        print(f"\n  Cosine similarities:")
        print(f"    revenue vs pipeline:  {sim_01:.4f}")
        print(f"    revenue vs risk:      {sim_02:.4f}")
        print(f"    pipeline vs risk:     {sim_12:.4f}")
        if svc.provider == "local":
            distinct_pairs = len({round(sim_01, 6), round(sim_02, 6), round(sim_12, 6)}) > 1
            check("Local embeddings produce non-identical similarity scores", distinct_pairs)
        else:
            check("Semantic similarities are meaningful (all > 0)", min(sim_01, sim_02, sim_12) > 0)

    except Exception as e:
        check("Embedding API call succeeded", False, str(e))

    # ── 3. Claude Agent SDK ─────────────────────────────────────────────
    section("3. Claude Agent SDK")

    if not use_llm:
        print("  RESEARCH_USE_LLM=false — skipping Claude Agent SDK test.")
        print("  Set RESEARCH_USE_LLM=true and ANTHROPIC_API_KEY in .env to enable.")
        check("SDK test skipped (use_llm=false)", True)
    else:
        try:
            from src.models import get_company
            from src.services.agent import ResearchAgent
            from src.services.mcp_server import ResearchToolServer
            from src.tests.helpers import build_soc_docs, populate_store

            agent = ResearchAgent(use_llm=True)
            soc = get_company("SOC US")
            print(f"  Model: {settings.anthropic_model}")
            print("\n  Running the real ResearchAgent against a fixture corpus...")
            with TemporaryDirectory() as tmp:
                store, _ = populate_store(tmp, soc, build_soc_docs(soc))
                tool_server = ResearchToolServer(soc, store)
                fallback = "__FALLBACK__"
                result = await agent.run(
                    phase="followup",
                    variables={
                        "company_name": soc.name,
                        "ticker": soc.ticker,
                        "question": "What do the ingested materials say about pipeline permits?",
                    },
                    tool_server=tool_server,
                    fallback_markdown=fallback,
                )
            check("ResearchAgent returned text", bool(result.text.strip()))
            check("ResearchAgent did not fall back", result.text != fallback)
            show("ResearchAgent response", result.text)

        except Exception as e:
            check("Claude Agent SDK call succeeded", False, str(e))

    # ── 4. Research Tool Surface ────────────────────────────────────────
    section("4. Research Tool Surface")

    try:
        from src.models import get_company
        from src.services.mcp_server import TOOLS_ANTHROPIC, ResearchToolServer
        from src.tests.helpers import build_soc_docs, populate_store

        print(f"  Tools available: {len(TOOLS_ANTHROPIC)}")
        print(f"  Tool names: {[t['name'] for t in TOOLS_ANTHROPIC]}")

        soc = get_company("SOC US")
        with TemporaryDirectory() as tmp:
            store, _ = populate_store(tmp, soc, build_soc_docs(soc))
            server = ResearchToolServer(soc, store)
            results = json.loads(server.call("search_corpus", {"query": "pipeline permits", "limit": 2}))

        check("Tool catalog has entries", len(TOOLS_ANTHROPIC) == 8)
        check("Tool server returns search results", len(results) > 0)
        show("Top search result", results[0]["text"][:200] if results else "NONE")
    except Exception as e:
        check("Research tool surface is healthy", False, str(e))

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  RESULTS: {_pass} passed, {_fail} failed")
    print(f"{'='*70}\n")

    if _fail > 0:
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
