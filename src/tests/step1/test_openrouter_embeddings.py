#!/usr/bin/env python3
"""Test: OpenRouter embeddings via openai-compatible endpoint.

Verifies that OpenRouter's text-embedding-3-small works end-to-end:
single query, batch, dimensions, and semantic similarity vs local hash.

Run:
    uv run python -m src.tests.step1.test_openrouter_embeddings
"""

import math
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)

from src.tests.helpers import section, check, show, report
from src.services.embeddings import EmbeddingService


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def main():
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("SKIP: OPENROUTER_API_KEY not set in .env")
        return

    print(f"OpenRouter key: {api_key[:12]}...")
    print()

    # ── OpenRouter embedding service ──────────────────────────────
    openrouter = EmbeddingService(
        provider="openai_compatible",
        model="openai/text-embedding-3-small",
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        dimensions=1536,
    )

    # ── Local hash embedding service (for comparison) ─────────────
    local = EmbeddingService(
        provider="local",
        dimensions=256,
    )

    # ── 1. Basic call ─────────────────────────────────────────────
    section("1. Single Query — OpenRouter")
    vec = openrouter.embed_query("offshore oil production risk factors")
    check(f"Returned {len(vec)} dimensions", len(vec) == 1536)
    norm = math.sqrt(sum(x * x for x in vec))
    check(f"L2-normalized (norm={norm:.4f})", abs(norm - 1.0) < 0.05)
    show("First 5 values", [round(v, 6) for v in vec[:5]])

    # ── 2. Batch ──────────────────────────────────────────────────
    section("2. Batch Embedding — 3 finance texts")
    texts = [
        "Revenue was $150 million for fiscal year 2024, a decrease from $200 million in 2023",
        "The company received pipeline permits from PHMSA for the Santa Ynez unit",
        "Going concern qualification from auditors citing substantial doubt about continued operations",
    ]
    vecs = openrouter.embed_texts(texts)
    check(f"Batch returned {len(vecs)} vectors", len(vecs) == 3)
    for i, v in enumerate(vecs):
        check(f"  Text {i+1}: {len(v)} dims", len(v) == 1536)

    # ── 3. Semantic similarity (the key advantage) ────────────────
    section("3. Semantic Similarity — OpenRouter vs Local")

    # These two texts mean the same thing but use different words:
    text_a = "The company may not be able to continue operating"
    text_b = "Going concern risk threatens the firm's survival"
    text_unrelated = "Best chocolate cake recipe with vanilla frosting"

    or_a = openrouter.embed_query(text_a)
    or_b = openrouter.embed_query(text_b)
    or_c = openrouter.embed_query(text_unrelated)

    lo_a = local.embed_query(text_a)
    lo_b = local.embed_query(text_b)
    lo_c = local.embed_query(text_unrelated)

    or_sim_related = cosine(or_a, or_b)
    or_sim_unrelated = cosine(or_a, or_c)
    lo_sim_related = cosine(lo_a, lo_b)
    lo_sim_unrelated = cosine(lo_a, lo_c)

    print()
    print(f"  Texts:")
    print(f"    A: \"{text_a}\"")
    print(f"    B: \"{text_b}\"")
    print(f"    C: \"{text_unrelated}\"")
    print()
    print(f"  OpenRouter (semantic):")
    print(f"    A vs B (same meaning, different words): {or_sim_related:.4f}")
    print(f"    A vs C (unrelated):                     {or_sim_unrelated:.4f}")
    print(f"    Gap:                                    {or_sim_related - or_sim_unrelated:+.4f}")
    print()
    print(f"  Local hash (token overlap):")
    print(f"    A vs B (same meaning, different words): {lo_sim_related:.4f}")
    print(f"    A vs C (unrelated):                     {lo_sim_unrelated:.4f}")
    print(f"    Gap:                                    {lo_sim_related - lo_sim_unrelated:+.4f}")
    print()

    check(
        "OpenRouter: related texts score higher than unrelated",
        or_sim_related > or_sim_unrelated,
    )

    or_gap = or_sim_related - or_sim_unrelated
    lo_gap = lo_sim_related - lo_sim_unrelated
    check(
        f"OpenRouter gap ({or_gap:.4f}) > local hash gap ({lo_gap:.4f})",
        or_gap > lo_gap,
        "This proves semantic embeddings understand meaning, not just token overlap",
    )

    # ── 4. Real retrieval scenario ────────────────────────────────
    section("4. Retrieval Scenario — 'bankruptcy risk'")
    query = "What is the bankruptcy risk?"
    chunks = [
        "Going concern qualification from the independent auditors",     # semantically relevant
        "Revenue decreased 15% year-over-year to $340 million",          # somewhat relevant
        "The board approved a quarterly dividend of $0.25 per share",    # not relevant
        "Environmental remediation costs estimated at $50 million",      # somewhat relevant
    ]

    q_vec = openrouter.embed_query(query)
    scores = [(cosine(q_vec, openrouter.embed_query(c)), c[:60]) for c in chunks]
    scores.sort(reverse=True)

    print()
    print(f"  Query: \"{query}\"")
    print(f"  Ranked results:")
    for i, (score, text) in enumerate(scores):
        print(f"    #{i+1}  {score:.4f}  {text}...")

    check(
        "Going concern ranks #1 for 'bankruptcy risk'",
        "going concern" in scores[0][1].lower(),
    )
    check(
        "Dividend ranks last (least relevant)",
        "dividend" in scores[-1][1].lower(),
    )

    report()


if __name__ == "__main__":
    main()
