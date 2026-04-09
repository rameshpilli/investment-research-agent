"""
Embedding Service
==================

Provides a small embedding layer:

- deterministic local embeddings for offline runs and tests
- OpenAI-compatible embeddings for real semantic retrieval

Key class:
- EmbeddingService -- routes embedding calls to the configured provider
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[a-z0-9]{2,}")

KNOWN_MODEL_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
}


class EmbeddingService:
    """Embedding provider wrapper.

    Supported modes:
    - `local`: deterministic offline embeddings for tests and no-key usage
    - `openai` / `openai_compatible`: OpenAI-style `/embeddings` endpoint
    """

    def __init__(
        self,
        dimensions: int | None = None,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.provider = (provider or settings.embedding_provider).strip().lower()
        self.model = model or settings.embedding_model
        self.api_key = (
            api_key
            or settings.embedding_api_key
            or self._env_or_none("OPENROUTER_API_KEY")
            or self._env_or_none("OPENAI_API_KEY")
        )
        self.base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.embedding_timeout_seconds

        if self.provider == "voyage":
            logger.warning(
                "RESEARCH_EMBEDDING_PROVIDER=voyage is no longer supported. "
                "Using local hash embeddings.",
            )
            self.provider = "local"
            self.model = "local-hash-v1"

        # Auto-fallback: if configured for OpenRouter/OpenAI but no API key
        # is available, fall back to local hash embeddings gracefully.
        if self.provider in {"openai", "openai_compatible"} and not self.api_key:
            logger.warning(
                "Embedding provider is '%s' but no API key found "
                "(checked RESEARCH_EMBEDDING_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY). "
                "Falling back to local hash embeddings.",
                self.provider,
            )
            self.provider = "local"
            self.model = "local-hash-v1"

        self.dimensions = dimensions or self._default_dimensions()

        # Warn if configured dimensions don't match the known model dimensions
        if self.provider != "local" and self.model in KNOWN_MODEL_DIMENSIONS:
            expected = KNOWN_MODEL_DIMENSIONS[self.model]
            if self.dimensions != expected:
                logger.warning(
                    "Embedding dimensions mismatch: model %s expects %d but configured with %d. "
                    "Set RESEARCH_EMBEDDING_DIMENSIONS=%d or Qdrant collection creation will fail.",
                    self.model, expected, self.dimensions, expected,
                )

    def embed_texts(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        if self.provider == "local":
            return [self._embed_local(text) for text in texts]
        if self.provider in {"openai", "openai_compatible"}:
            # Batch API calls to avoid hitting payload/token limits
            all_embeddings: list[list[float]] = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                all_embeddings.extend(self._embed_openai_compatible(batch))
            return all_embeddings
        raise ValueError(f"Unsupported embedding provider '{self.provider}'")

    def embed_query(self, query: str) -> list[float]:
        if self.provider == "local":
            return self._embed_local(query)
        if self.provider in {"openai", "openai_compatible"}:
            return self._embed_openai_compatible([query])[0]
        raise ValueError(f"Unsupported embedding provider '{self.provider}'")

    def _default_dimensions(self) -> int:
        if self.provider == "local":
            return settings.embedding_dimensions
        return KNOWN_MODEL_DIMENSIONS.get(self.model, settings.embedding_dimensions)

    def _embed_openai_compatible(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise ValueError(
                "RESEARCH_EMBEDDING_API_KEY, OPENROUTER_API_KEY, or OPENAI_API_KEY "
                "is required for openai-compatible embeddings"
            )

        payload: dict[str, Any] = {"input": texts, "model": self.model}
        response = self._post_json(
            url=f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload=payload,
        )
        data = sorted(response.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]

    def _post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        full_headers = {"Content-Type": "application/json", **headers}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=full_headers, json=payload)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _env_or_none(name: str) -> str | None:
        import os

        value = os.getenv(name)
        return value or None

    def _embed_local(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        normalized = " ".join(text.lower().split())
        tokens = TOKEN_RE.findall(normalized)

        if not tokens:
            vector[0] = 1.0
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 10) / 20.0
            vector[index] += sign * weight

        for index in range(max(0, len(normalized) - 2)):
            trigram = normalized[index : index + 3]
            digest = hashlib.md5(trigram.encode("utf-8")).digest()  # noqa: S324
            trigram_index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[trigram_index] += sign * 0.05

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [value / norm for value in vector]
