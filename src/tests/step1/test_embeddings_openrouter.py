#!/usr/bin/env python3
"""Step 1 Test: OpenRouter / OpenAI-compatible embeddings via real API.

This test calls an external embedding API to verify that the
openai_compatible provider path works end-to-end: single query,
batch, correct dimensions, and meaningful cosine similarity.

Requires one of these env vars:
    OPENROUTER_API_KEY   — OpenRouter key (uses their /embeddings endpoint)
    OPENAI_API_KEY       — OpenAI key (uses api.openai.com)
    RESEARCH_EMBEDDING_API_KEY — explicit override

Run:
    OPENROUTER_API_KEY=sk-or-... uv run python -m src.tests.step1.test_embeddings_openrouter

If no key is set, the test prints a skip message and exits cleanly.
"""

import math
import os

from src.tests.helpers import section, check, show, report
from src.services.embeddings import EmbeddingService


def main():
    # Resolve key — same fallback chain as EmbeddingService
    api_key = (
        os.getenv("RESEARCH_EMBEDDING_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )

    if not api_key:
        print("SKIP: No embedding API key found.")
        print("Set OPENROUTER_API_KEY, OPENAI_API_KEY, or RESEARCH_EMBEDDING_API_KEY to run this test.")
        return

    # Detect base URL from which key is set
    if os.getenv("OPENROUTER_API_KEY"):
        base_url = "https://openrouter.ai/api/v1"
        source = "OpenRouter"
    else:
        base_url = "https://api.openai.com/v1"
        source = "OpenAI"

    model = "text-embedding-3-small"
    expected_dims = 1536

    print(f"Using {source} ({base_url}) with model {model}")
    print()

    svc = EmbeddingService(
        provider="openai_compatible",
        model=model,
        api_key=api_key,
        base_url=base_url,
        dimensions=expected_dims,
    )

    # ── Single query ──────────────────────────────────────────────
    section("Single Query Embedding")
    vec = svc.embed_query("offshore oil production risk factors")
    check(f"Returned {len(vec)} dimensions (expected {expected_dims})", len(vec) == expected_dims)

    norm = math.sqrt(sum(x * x for x in vec))
    check(f"L2-normalized (norm={norm:.4f})", abs(norm - 1.0) < 0.05)
    show("First 5 values", [round(v, 6) for v in vec[:5]])

    # ── Batch embedding ───────────────────────────────────────────
    section("Batch Embedding (3 texts)")
    texts = [
        "Revenue was $150 million for fiscal year 2024",
        "The company received pipeline permits from PHMSA",
        "Going concern qualification from auditors due to substantial doubt",
    ]
    vecs = svc.embed_texts(texts)
    check(f"Batch returned {len(vecs)} vectors", len(vecs) == 3)
    for i, v in enumerate(vecs):
        check(f"  Text {i+1}: {len(v)} dims", len(v) == expected_dims)

    # ── Cosine similarity ─────────────────────────────────────────
    section("Semantic Similarity (cosine)")

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0

    # Related financial texts should be more similar than unrelated ones
    sim_revenue_risk = cosine(vecs[0], vecs[2])
    sim_pipeline_risk = cosine(vecs[1], vecs[2])
    show("revenue vs going-concern", f"{sim_revenue_risk:.4f}")
    show("pipeline vs going-concern", f"{sim_pipeline_risk:.4f}")

    # Compare against something completely unrelated
    unrelated_vec = svc.embed_query("chocolate cake recipe with vanilla frosting")
    sim_unrelated = cosine(vecs[0], unrelated_vec)
    show("revenue vs chocolate cake", f"{sim_unrelated:.4f}")

    check(
        "Finance texts more similar to each other than to unrelated",
        sim_revenue_risk > sim_unrelated,
    )

    # ── Query vs document distinction ─────────────────────────────
    section("Query vs Document Embedding")
    query_vec = svc.embed_query("What are the risk factors?")
    doc_vec = svc.embed_texts(["Risk factors include environmental liability and regulatory uncertainty"])[0]
    sim_relevant = cosine(query_vec, doc_vec)
    sim_irrelevant = cosine(query_vec, unrelated_vec)
    show("query-to-relevant-doc similarity", f"{sim_relevant:.4f}")
    show("query-to-irrelevant-doc similarity", f"{sim_irrelevant:.4f}")
    check("Relevant doc scores higher than irrelevant", sim_relevant > sim_irrelevant)

    report()


if __name__ == "__main__":
    main()
