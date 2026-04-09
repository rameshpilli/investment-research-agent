"""
Application Configuration
==========================

Centralizes all runtime settings into a single frozen dataclass.
Values load from environment variables with sensible defaults.

Key class:
- Settings -- paths, chunking, embedding, LLM, and MCP tuning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Load .env file if it exists (for local development; Docker Compose loads it separately)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        # python-dotenv not installed — read manually
        for line in _env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if key and key not in os.environ:
                    os.environ[key] = value


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    parts = tuple(item.strip() for item in value.split(",") if item.strip())
    return parts or default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _default_user_agent_email() -> str:
    user = os.getenv("USER") or os.getenv("USERNAME")
    return f"{user}@localhost" if user else "local-user@localhost"


def _src_root() -> Path:
    """Resolve the src/ package root.

    After ``pip install .`` the package lives under site-packages, but data,
    output, and prompt directories are expected under the *source tree* (e.g.
    /app/src/ inside Docker).  Honour ``RESEARCH_SRC_ROOT`` when set so that
    Docker and other installed environments can point at the right tree.
    Locally (running from the repo checkout) ``__file__`` already works.
    """
    env = os.getenv("RESEARCH_SRC_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parent


@dataclass(frozen=True)
class Settings:
    # ── Paths ──────────────────────────────────────────────────────
    repo_root: Path = _src_root().parent
    data_dir: Path = _src_root() / "data"
    raw_data_dir: Path = _src_root() / "data" / "raw"
    processed_data_dir: Path = _src_root() / "data" / "processed"
    output_dir: Path = _src_root() / "output"
    prompts_dir: Path = _src_root() / "prompts"
    # Legacy — kept for backward-compat with old flat input_files/ layout
    input_files_dir: Path = _src_root() / "input_files"

    # ── Chunking ───────────────────────────────────────────────────
    chunk_size: int = _env_int("RESEARCH_CHUNK_SIZE", 1200)
    chunk_overlap: int = _env_int("RESEARCH_CHUNK_OVERLAP", 200)
    max_docs_per_source: int = _env_int("RESEARCH_MAX_DOCS_PER_SOURCE", 30)
    max_chunks_per_company: int = _env_int("RESEARCH_MAX_CHUNKS_PER_COMPANY", 500)

    # ── Data sources ───────────────────────────────────────────────
    years_of_filings: int = _env_int("RESEARCH_FILINGS_YEARS", 3)
    years_of_price_history: int = _env_int("RESEARCH_PRICE_HISTORY_YEARS", 3)
    news_result_limit: int = _env_int("RESEARCH_NEWS_RESULT_LIMIT", 20)
    user_agent_name: str = _env_str("RESEARCH_USER_AGENT_NAME", "investment-research/0.1")
    user_agent_email: str = _env_str("RESEARCH_USER_AGENT_EMAIL", _default_user_agent_email())

    # ── Embeddings ─────────────────────────────────────────────────
    embedding_provider: str = _env_str("RESEARCH_EMBEDDING_PROVIDER", "local")
    embedding_model: str = _env_str("RESEARCH_EMBEDDING_MODEL", "local-hash-v1")
    embedding_dimensions: int = _env_int("RESEARCH_EMBEDDING_DIMENSIONS", 256)
    embedding_api_key: str | None = os.getenv("RESEARCH_EMBEDDING_API_KEY")
    embedding_base_url: str = _env_str("RESEARCH_EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    embedding_timeout_seconds: int = _env_int("RESEARCH_EMBEDDING_TIMEOUT_SECONDS", 30)

    # ── Qdrant ─────────────────────────────────────────────────────
    qdrant_url: str | None = os.getenv("RESEARCH_QDRANT_URL")
    qdrant_collection_prefix: str = _env_str("RESEARCH_QDRANT_COLLECTION_PREFIX", "src")

    # ── Claude Agent SDK ───────────────────────────────────────────
    use_llm: bool = _env_bool("RESEARCH_USE_LLM", False)
    anthropic_model: str = _env_str("RESEARCH_ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    anthropic_fallback_model: str | None = _env_str("RESEARCH_ANTHROPIC_FALLBACK_MODEL", "") or None
    llm_max_tool_rounds: int = _env_int("RESEARCH_LLM_MAX_TOOL_ROUNDS", 12)
    phase1_max_budget_usd: float = _env_float("RESEARCH_PHASE1_MAX_BUDGET_USD", 2.0)
    phase2_max_budget_usd: float = _env_float("RESEARCH_PHASE2_MAX_BUDGET_USD", 2.0)
    phase3_max_budget_usd: float = _env_float("RESEARCH_PHASE3_MAX_BUDGET_USD", 1.0)
    followup_max_budget_usd: float = _env_float("RESEARCH_FOLLOWUP_MAX_BUDGET_USD", 0.5)

    # ── Research tuning ────────────────────────────────────────────
    dossier_topics_per_query: int = _env_int("RESEARCH_DOSSIER_TOPICS_PER_QUERY", 4)
    followup_retrieval_limit: int = _env_int("RESEARCH_FOLLOWUP_RETRIEVAL_LIMIT", 6)

    # ── MCP tool limits ────────────────────────────────────────────
    mcp_search_default_limit: int = _env_int("RESEARCH_MCP_SEARCH_LIMIT", 8)
    mcp_document_char_limit: int = _env_int("RESEARCH_MCP_DOCUMENT_CHAR_LIMIT", 8000)
    mcp_section_fallback_chunks: int = _env_int("RESEARCH_MCP_SECTION_FALLBACK_CHUNKS", 3)
    mcp_section_match_limit: int = _env_int("RESEARCH_MCP_SECTION_MATCH_LIMIT", 5)
    vector_search_limit: int = _env_int("RESEARCH_VECTOR_SEARCH_LIMIT", 20)
    topic_chunks_per_topic: int = _env_int("RESEARCH_TOPIC_CHUNKS_PER_TOPIC", 10)
    citation_retrieval_limit: int = _env_int("RESEARCH_CITATION_RETRIEVAL_LIMIT", 3)

    # ── UI / MCP ─────────────────────────────────────────────────────
    cors_origins: tuple[str, ...] = _env_csv("RESEARCH_CORS_ORIGINS", ("*",))
    mcp_port: int = _env_int("RESEARCH_MCP_PORT", 8081)
    mcp_url: str = _env_str("RESEARCH_MCP_URL", "")

    @property
    def sec_user_agent(self) -> str:
        return f"{self.user_agent_name} {self.user_agent_email}"


settings = Settings()
