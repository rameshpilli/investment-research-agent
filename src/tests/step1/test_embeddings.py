#!/usr/bin/env python3
"""Step 1 Test: Embedding Service — deterministic hashing, dimensions, batch."""

import math
from src.tests.helpers import section, check, report
from src.services.embeddings import EmbeddingService


def main():
    svc = EmbeddingService(
        provider="local",
        model="local-hash-v1",
        dimensions=256,
    )

    section("Basics")
    v1 = svc.embed_query("offshore oil production revenue")
    check(f"Dimensions: {len(v1)}", len(v1) == 256)
    check("Deterministic", v1 == svc.embed_query("offshore oil production revenue"))
    check("Different inputs differ", v1 != svc.embed_query("python interview questions"))
    norm = math.sqrt(sum(x * x for x in v1))
    check(f"L2-normalized (norm={norm:.4f})", abs(norm - 1.0) < 0.01)

    section("Batch")
    vecs = svc.embed_texts(["a", "b", "c"])
    check("3 vectors returned", len(vecs) == 3)

    section("Edge Cases")
    check("Empty string", len(svc.embed_query("")) == 256)
    check("Short string", len(svc.embed_query("hi")) == 256)
    check("Very long string", len(svc.embed_query("word " * 5000)) == 256)

    report()

if __name__ == "__main__":
    main()
