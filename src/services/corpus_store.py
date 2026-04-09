"""
Corpus Store Service
=====================

Manages the full document-to-vector pipeline: connector orchestration,
text chunking, SQLite-backed document storage, and Qdrant vector
indexing. Provides prepare, refresh, and query operations that the
pipeline steps rely on.

Key class:
- CorpusStore -- orchestrates connectors, chunks text, persists to
  SQLite, and indexes/queries Qdrant for semantic retrieval
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from src.config import settings
from src.connectors import (
    BaseResearchConnector,
    CompanyIRConnector,
    EdgarConnector,
    NewswebConnector,
    WebSearchConnector,
    YFinanceConnector,
)
from src.models.companies import CompanyConfig
from src.models.documents import (
    CHUNKING_VERSION,
    PARSER_VERSION,
    Chunk,
    CorpusManifest,
    CorpusSnapshot,
    Document,
    SourceStatus,
    utc_now_iso,
)
from src.services.embeddings import EmbeddingService

DEFAULT_VECTOR_SEARCH_LIMIT = settings.vector_search_limit
DEFAULT_TOPIC_CHUNKS_PER_TOPIC = settings.topic_chunks_per_topic


def _json_dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _date_key(document: Document) -> str:
    return document.filing_date or document.published_at or document.as_of_date or ""


def chunk_document(document: Document) -> list[Chunk]:
    text = document.raw_text.strip()
    if not text and document.structured_payload is not None:
        text = json.dumps(document.structured_payload, default=str, indent=2)
    if not text:
        return []

    chunks: list[Chunk] = []
    start = 0
    chunk_index = 0
    while start < len(text):
        end = min(len(text), start + settings.chunk_size)
        window = text[start:end].strip()
        if window:
            chunks.append(
                Chunk.create(
                    doc=document,
                    chunk_index=chunk_index,
                    text=window,
                    page_or_section=document.metadata.get("section"),
                    char_start=start,
                    char_end=end,
                )
            )
            chunk_index += 1
        if end >= len(text):
            break
        start = max(0, end - settings.chunk_overlap)
    return chunks


@dataclass
class CorpusStore:
    root_dir: Path = settings.processed_data_dir
    output_dir: Path = settings.output_dir
    embedding_service: EmbeddingService = field(default_factory=EmbeddingService)
    _qdrant_clients: dict[str, QdrantClient] = field(default_factory=dict, init=False, repr=False)

    def close(self) -> None:
        """Close all cached Qdrant clients. Call this before process exit."""
        for slug, client in self._qdrant_clients.items():
            try:
                client.close()
            except Exception as e:
                logger.warning("Failed to close Qdrant client for %s: %s", slug, e)
        self._qdrant_clients.clear()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def corpus_dir(self, profile: CompanyConfig) -> Path:
        path = self.root_dir / profile.slug
        path.mkdir(parents=True, exist_ok=True)
        return path

    def output_company_dir(self, profile: CompanyConfig) -> Path:
        path = self.output_dir / profile.slug
        path.mkdir(parents=True, exist_ok=True)
        return path

    def manifest_path(self, profile: CompanyConfig) -> Path:
        return self.corpus_dir(profile) / "manifest.json"

    def sqlite_path(self, profile: CompanyConfig) -> Path:
        return self.corpus_dir(profile) / "store.db"

    def qdrant_path(self, profile: CompanyConfig) -> Path:
        path = self.corpus_dir(profile) / "qdrant"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def corpus_exists(self, profile: CompanyConfig) -> bool:
        return self.manifest_path(profile).exists() and self.sqlite_path(profile).exists()

    def load_manifest(self, profile: CompanyConfig) -> CorpusManifest | None:
        path = self.manifest_path(profile)
        if not path.exists():
            return None
        return CorpusManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def save_manifest(self, profile: CompanyConfig, manifest: CorpusManifest) -> None:
        self.manifest_path(profile).write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def load_corpus(self, profile: CompanyConfig) -> CorpusSnapshot:
        manifest = self.load_manifest(profile)
        if manifest is None:
            raise FileNotFoundError(f"Corpus manifest missing for {profile.ticker}")
        return CorpusSnapshot(
            manifest=manifest,
            documents=self.get_all_documents(profile),
            chunks=self.get_all_chunks(profile),
        )

    async def prepare_corpus(
        self,
        profile: CompanyConfig,
        connectors: list[BaseResearchConnector] | None = None,
    ) -> CorpusSnapshot:
        connectors = connectors or build_default_connectors(profile)
        statuses, documents_by_source = await self._fetch_all_sources(profile, connectors, refresh_reasons={})

        all_documents = [doc for docs in documents_by_source.values() for doc in docs]
        self._replace_all_documents(profile, all_documents)

        all_chunks = self._rebuild_all_chunks(profile, all_documents)
        now = utc_now_iso()
        manifest = CorpusManifest(
            ticker=profile.ticker,
            slug=profile.slug,
            created_at=now,
            last_refreshed_at=now,
            sources=statuses,
            total_documents=len(all_documents),
            total_chunks=len(all_chunks),
            metadata={"company_name": profile.name},
        )
        self.save_manifest(profile, manifest)
        return CorpusSnapshot(manifest=manifest, documents=all_documents, chunks=all_chunks)

    async def refresh_corpus(
        self,
        profile: CompanyConfig,
        connectors: list[BaseResearchConnector] | None = None,
    ) -> CorpusSnapshot:
        if not self.corpus_exists(profile):
            return await self.prepare_corpus(profile, connectors=connectors)

        connectors = connectors or build_default_connectors(profile)
        manifest = self.load_manifest(profile)
        assert manifest is not None

        if manifest.parser_version != PARSER_VERSION or manifest.chunking_version != CHUNKING_VERSION:
            documents = self.get_all_documents(profile)
            chunks = self._rebuild_all_chunks(profile, documents)
            updated = CorpusManifest(
                ticker=profile.ticker,
                slug=profile.slug,
                created_at=manifest.created_at,
                last_refreshed_at=utc_now_iso(),
                sources=manifest.sources,
                total_documents=len(documents),
                total_chunks=len(chunks),
                metadata=manifest.metadata,
            )
            self.save_manifest(profile, updated)
            return CorpusSnapshot(manifest=updated, documents=documents, chunks=chunks)

        previous_status = manifest.source_map()
        statuses: list[SourceStatus] = []
        for connector in connectors:
            previous = previous_status.get(connector.source_key)
            if previous and connector.refresh_hours is not None and not connector.is_stale(previous.last_fetched):
                statuses.append(
                    SourceStatus(
                        connector=connector.source_key,
                        last_fetched=previous.last_fetched,
                        doc_count=previous.doc_count,
                        status="fresh",
                        error_message=None,
                        refresh_reason="reused_cached_source",
                    )
                )
                continue

            try:
                documents = await connector.fetch_documents(profile)
            except Exception as exc:  # pragma: no cover - defensive for live connectors
                statuses.append(
                    SourceStatus(
                        connector=connector.source_key,
                        last_fetched=utc_now_iso(),
                        doc_count=previous.doc_count if previous else 0,
                        status="error",
                        error_message=str(exc),
                        refresh_reason="fetch_error",
                    )
                )
                continue

            source_documents = documents[: settings.max_docs_per_source]
            current = {doc.doc_id: doc for doc in self.get_documents_for_source(profile, connector.source_key)}
            fetched_ids = {doc.doc_id for doc in source_documents}

            changed_chunks: list[Chunk] = []
            for document in source_documents:
                existing = current.get(document.doc_id)
                self.store_documents(profile, [document])
                if existing is None or existing.content_hash != document.content_hash or existing.parser_version != document.parser_version:
                    self.delete_chunks_for_doc(profile, document.doc_id)
                    doc_chunks = chunk_document(document)
                    self.store_chunks(profile, doc_chunks)
                    changed_chunks.extend(doc_chunks)

            # Remove docs that disappeared upstream
            removed_ids = set(current) - fetched_ids
            for removed_id in removed_ids:
                logger.info("Removing stale document %s from %s", removed_id, connector.source_key)
                self.delete_chunks_for_doc(profile, removed_id)
                self.delete_document(profile, removed_id)

            if changed_chunks:
                documents_for_chunks = {
                    document.doc_id: document
                    for document in source_documents
                    if any(chunk.doc_id == document.doc_id for chunk in changed_chunks)
                }
                self.index_chunks(profile, changed_chunks, documents_for_chunks)

            refresh_reason = "checked_upstream_no_new_documents"
            if source_documents and (set(current) != fetched_ids or changed_chunks or removed_ids):
                refresh_reason = "stale_source_refresh"

            statuses.append(
                SourceStatus(
                    connector=connector.source_key,
                    last_fetched=utc_now_iso(),
                    doc_count=len(self.get_documents_for_source(profile, connector.source_key)),
                    status="fresh",
                    error_message=None,
                    refresh_reason=refresh_reason,
                )
            )

        all_documents = self.get_all_documents(profile)
        if self.get_chunk_count(profile) > settings.max_chunks_per_company:
            self._rebuild_all_chunks(profile, all_documents)

        all_chunks = self.get_all_chunks(profile)
        updated = CorpusManifest(
            ticker=profile.ticker,
            slug=profile.slug,
            created_at=manifest.created_at,
            last_refreshed_at=utc_now_iso(),
            sources=statuses,
            total_documents=len(all_documents),
            total_chunks=len(all_chunks),
            metadata=manifest.metadata or {"company_name": profile.name},
        )
        self.save_manifest(profile, updated)
        return CorpusSnapshot(manifest=updated, documents=all_documents, chunks=all_chunks)

    def get_all_documents(self, profile: CompanyConfig) -> list[Document]:
        with self._connect(profile) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM documents
                ORDER BY filing_date DESC, published_at DESC, title ASC
                """
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def get_all_chunks(self, profile: CompanyConfig) -> list[Chunk]:
        with self._connect(profile) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM chunks
                ORDER BY doc_id ASC, chunk_index ASC
                """
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_chunks_for_doc(self, profile: CompanyConfig, doc_id: str) -> list[Chunk]:
        with self._connect(profile) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM chunks
                WHERE doc_id = ?
                ORDER BY chunk_index ASC
                """,
                (doc_id,),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def get_document(self, profile: CompanyConfig, doc_id: str) -> Document | None:
        with self._connect(profile) as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def get_doc_by_content_hash(self, profile: CompanyConfig, content_hash: str) -> Document | None:
        with self._connect(profile) as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def get_documents_for_source(self, profile: CompanyConfig, source_key: str) -> list[Document]:
        with self._connect(profile) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM documents
                WHERE source_key = ?
                ORDER BY filing_date DESC, published_at DESC, title ASC
                """,
                (source_key,),
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def get_chunk_count(self, profile: CompanyConfig) -> int:
        with self._connect(profile) as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        return int(row["count"]) if row else 0

    def store_documents(self, profile: CompanyConfig, docs: list[Document]) -> None:
        if not docs:
            return
        rows = [
            (
                doc.doc_id,
                doc.ticker,
                doc.source_key,
                doc.source_name,
                doc.source_url,
                doc.doc_type,
                doc.filing_date,
                doc.published_at,
                doc.as_of_date,
                doc.title,
                doc.raw_text,
                _json_dump(doc.structured_payload),
                doc.content_hash,
                doc.ingested_at,
                doc.parser_version,
                _json_dump(doc.metadata),
            )
            for doc in docs
        ]
        with self._connect(profile) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO documents (
                    doc_id, ticker, source_key, source_name, source_url, doc_type,
                    filing_date, published_at, as_of_date, title, raw_text,
                    structured_payload, content_hash, ingested_at, parser_version, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def store_chunks(self, profile: CompanyConfig, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        rows = [
            (
                chunk.chunk_id,
                chunk.doc_id,
                chunk.ticker,
                chunk.chunk_index,
                chunk.text,
                chunk.page_or_section,
                chunk.char_start,
                chunk.char_end,
                chunk.chunking_version,
                _json_dump(chunk.metadata),
            )
            for chunk in chunks
        ]
        with self._connect(profile) as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO chunks (
                    chunk_id, doc_id, ticker, chunk_index, text, page_or_section,
                    char_start, char_end, chunking_version, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def delete_document(self, profile: CompanyConfig, doc_id: str) -> None:
        with self._connect(profile) as conn:
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            conn.commit()

    def delete_chunks_for_doc(self, profile: CompanyConfig, doc_id: str) -> None:
        # Delete from Qdrant first (idempotent) to avoid orphaned vectors
        # if the subsequent SQLite delete fails.
        client = self._qdrant_client(profile)
        collection_name = self._collection_name(profile)
        if client.collection_exists(collection_name):
            client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
                wait=True,
            )

        with self._connect(profile) as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.commit()

    def search_chunks(
        self,
        profile: CompanyConfig,
        query: str,
        limit: int = DEFAULT_VECTOR_SEARCH_LIMIT,
        filters: dict[str, str] | None = None,
    ) -> list[Chunk]:
        if not query.strip():
            return []

        client = self._qdrant_client(profile)
        self._ensure_collection(profile, client)
        response = client.query_points(
            collection_name=self._collection_name(profile),
            query=self.embedding_service.embed_query(query),
            limit=limit,
            with_payload=True,
            query_filter=self._build_filter(filters),
        )
        chunk_ids = [point.payload["chunk_id"] for point in response.points if point.payload and point.payload.get("chunk_id")]
        return self.get_chunks_by_ids(profile, chunk_ids)

    def search_chunks_by_topic(
        self,
        profile: CompanyConfig,
        topics: list[str],
        per_topic: int = DEFAULT_TOPIC_CHUNKS_PER_TOPIC,
    ) -> dict[str, list[Chunk]]:
        results: dict[str, list[Chunk]] = {}
        seen: set[str] = set()
        for topic in topics:
            topic_chunks: list[Chunk] = []
            for chunk in self.search_chunks(profile, topic, limit=max(per_topic * 2, per_topic)):
                if chunk.chunk_id in seen:
                    continue
                topic_chunks.append(chunk)
                seen.add(chunk.chunk_id)
                if len(topic_chunks) >= per_topic:
                    break
            results[topic] = topic_chunks
        return results

    def index_chunks(
        self,
        profile: CompanyConfig,
        chunks: list[Chunk],
        documents_by_id: dict[str, Document],
    ) -> None:
        if not chunks:
            return

        client = self._qdrant_client(profile)
        self._ensure_collection(profile, client)
        embeddings = self.embedding_service.embed_texts([chunk.text for chunk in chunks])
        points: list[PointStruct] = []
        for chunk, embedding in zip(chunks, embeddings):
            document = documents_by_id[chunk.doc_id]
            points.append(
                PointStruct(
                    id=self._qdrant_point_id(chunk.chunk_id),
                    vector=embedding,
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "doc_id": chunk.doc_id,
                        "doc_type": document.doc_type,
                        "source_name": document.source_name,
                        "source_key": document.source_key,
                        "filing_date": document.filing_date,
                        "page_or_section": chunk.page_or_section,
                        "text": chunk.text,
                    },
                )
            )
        client.upsert(
            collection_name=self._collection_name(profile),
            points=points,
            wait=True,
        )

    def get_chunks_by_ids(self, profile: CompanyConfig, chunk_ids: list[str]) -> list[Chunk]:
        if not chunk_ids:
            return []
        placeholders = ", ".join("?" for _ in chunk_ids)
        with self._connect(profile) as conn:
            rows = conn.execute(
                f"SELECT * FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
        by_id = {row["chunk_id"]: self._row_to_chunk(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

    async def _fetch_all_sources(
        self,
        profile: CompanyConfig,
        connectors: list[BaseResearchConnector],
        refresh_reasons: dict[str, str],
    ) -> tuple[list[SourceStatus], dict[str, list[Document]]]:
        results = await asyncio.gather(
            *(connector.fetch_documents(profile) for connector in connectors),
            return_exceptions=True,
        )

        statuses: list[SourceStatus] = []
        documents_by_source: dict[str, list[Document]] = {}
        for connector, result in zip(connectors, results, strict=True):
            if isinstance(result, Exception):
                statuses.append(
                    SourceStatus(
                        connector=connector.source_key,
                        last_fetched=utc_now_iso(),
                        doc_count=0,
                        status="error",
                        error_message=str(result),
                        refresh_reason=refresh_reasons.get(connector.source_key, "fetch_error"),
                    )
                )
                continue
            # Local files are free — don't cap them.  Online sources get the configured limit.
            max_docs = len(result) if connector.source_key == "local_files" else settings.max_docs_per_source
            documents = result[:max_docs]
            documents_by_source[connector.source_key] = documents
            statuses.append(
                SourceStatus(
                    connector=connector.source_key,
                    last_fetched=utc_now_iso(),
                    doc_count=len(documents),
                    status="fresh",
                    error_message=None,
                    refresh_reason=refresh_reasons.get(connector.source_key, "initial_build"),
                )
            )
        return statuses, documents_by_source

    def _replace_all_documents(self, profile: CompanyConfig, documents: list[Document]) -> None:
        rows = [
            (
                doc.doc_id, doc.ticker, doc.source_key, doc.source_name,
                doc.source_url, doc.doc_type, doc.filing_date, doc.published_at,
                doc.as_of_date, doc.title, doc.raw_text,
                _json_dump(doc.structured_payload), doc.content_hash,
                doc.ingested_at, doc.parser_version, _json_dump(doc.metadata),
            )
            for doc in documents
        ]
        with self._connect(profile) as conn:
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            conn.executemany(
                """
                INSERT OR REPLACE INTO documents (
                    doc_id, ticker, source_key, source_name, source_url, doc_type,
                    filing_date, published_at, as_of_date, title, raw_text,
                    structured_payload, content_hash, ingested_at, parser_version, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

    def _rebuild_all_chunks(self, profile: CompanyConfig, documents: list[Document]) -> list[Chunk]:
        with self._connect(profile) as conn:
            conn.execute("DELETE FROM chunks")
            conn.commit()

        client = self._qdrant_client(profile)
        if client.collection_exists(self._collection_name(profile)):
            client.delete_collection(self._collection_name(profile))
        self._ensure_collection(profile, client)

        prioritized_documents = sorted(
            documents,
            key=lambda document: (_date_key(document), document.title),
            reverse=True,
        )
        all_chunks: list[Chunk] = []
        for document in prioritized_documents:
            doc_chunks = chunk_document(document)
            if len(all_chunks) + len(doc_chunks) > settings.max_chunks_per_company:
                remaining = settings.max_chunks_per_company - len(all_chunks)
                if remaining <= 0:
                    break
                doc_chunks = doc_chunks[:remaining]
            all_chunks.extend(doc_chunks)
            if len(all_chunks) >= settings.max_chunks_per_company:
                break

        self.store_chunks(profile, all_chunks)
        self.index_chunks(profile, all_chunks, {document.doc_id: document for document in documents})
        return all_chunks

    def _connect(self, profile: CompanyConfig) -> sqlite3.Connection:
        connection = sqlite3.connect(self.sqlite_path(profile))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                doc_type TEXT NOT NULL,
                filing_date TEXT,
                published_at TEXT,
                as_of_date TEXT,
                title TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                structured_payload TEXT,
                content_hash TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                metadata TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL REFERENCES documents(doc_id),
                ticker TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                page_or_section TEXT,
                char_start INTEGER,
                char_end INTEGER,
                chunking_version TEXT NOT NULL,
                metadata TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_documents_source_key ON documents(source_key);
            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            """
        )
        conn.commit()

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> Document:
        return Document(
            doc_id=row["doc_id"],
            ticker=row["ticker"],
            source_key=row["source_key"],
            source_name=row["source_name"],
            source_url=row["source_url"],
            doc_type=row["doc_type"],
            filing_date=row["filing_date"],
            published_at=row["published_at"],
            as_of_date=row["as_of_date"],
            title=row["title"],
            raw_text=row["raw_text"],
            structured_payload=json.loads(row["structured_payload"]) if row["structured_payload"] else None,
            content_hash=row["content_hash"],
            ingested_at=row["ingested_at"],
            parser_version=row["parser_version"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            ticker=row["ticker"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            page_or_section=row["page_or_section"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            chunking_version=row["chunking_version"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def _qdrant_client(self, profile: CompanyConfig) -> QdrantClient:
        if profile.slug in self._qdrant_clients:
            return self._qdrant_clients[profile.slug]
        if settings.qdrant_url:
            logger.info("Connecting to external Qdrant at %s for %s", settings.qdrant_url, profile.slug)
            client = QdrantClient(url=settings.qdrant_url)
        else:
            qdrant_path = self.qdrant_path(profile)
            logger.info("Using local Qdrant at %s for %s", qdrant_path, profile.slug)
            client = QdrantClient(path=str(qdrant_path))
        self._qdrant_clients[profile.slug] = client
        return client

    def _collection_name(self, profile: CompanyConfig) -> str:
        return f"{settings.qdrant_collection_prefix}_{profile.slug}"

    def _ensure_collection(self, profile: CompanyConfig, client: QdrantClient) -> None:
        collection_name = self._collection_name(profile)
        needs_reindex = False
        if client.collection_exists(collection_name):
            info = client.get_collection(collection_name)
            if info.points_count == 0:
                needs_reindex = True
        else:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.embedding_service.dimensions,
                    distance=Distance.COSINE,
                ),
                on_disk_payload=True,
            )
            needs_reindex = True

        if needs_reindex:
            self._reindex_from_sqlite(profile, client)

    def _reindex_from_sqlite(self, profile: CompanyConfig, client: QdrantClient) -> None:
        """Re-index vectors from SQLite chunks into Qdrant.

        This handles the case where the Qdrant instance (e.g. Docker) is
        empty but the SQLite store already has ingested chunks (e.g. from
        a prior local ingestion run).

        Note: upserts directly to *client* to avoid recursion through
        index_chunks → _ensure_collection.
        """
        chunks = self.get_all_chunks(profile)
        if not chunks:
            return
        documents = self.get_all_documents(profile)
        documents_by_id = {d.doc_id: d for d in documents}
        valid_chunks = [c for c in chunks if c.doc_id in documents_by_id]
        if not valid_chunks:
            return
        logger.info(
            "Qdrant empty for %s — re-indexing %d chunks from SQLite",
            profile.ticker, len(valid_chunks),
        )
        collection_name = self._collection_name(profile)
        batch_size = 128
        for i in range(0, len(valid_chunks), batch_size):
            batch = valid_chunks[i : i + batch_size]
            embeddings = self.embedding_service.embed_texts([c.text for c in batch])
            points = []
            for chunk, embedding in zip(batch, embeddings):
                doc = documents_by_id[chunk.doc_id]
                points.append(
                    PointStruct(
                        id=self._qdrant_point_id(chunk.chunk_id),
                        vector=embedding,
                        payload={
                            "chunk_id": chunk.chunk_id,
                            "doc_id": chunk.doc_id,
                            "doc_type": doc.doc_type,
                            "source_name": doc.source_name,
                            "source_key": doc.source_key,
                            "filing_date": doc.filing_date,
                            "page_or_section": chunk.page_or_section,
                            "text": chunk.text,
                        },
                    )
                )
            client.upsert(collection_name=collection_name, points=points, wait=True)
            logger.info("  indexed batch %d–%d / %d", i + 1, i + len(batch), len(valid_chunks))

    @staticmethod
    def _qdrant_point_id(chunk_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, chunk_id))

    @staticmethod
    def _build_filter(filters: dict[str, str] | None) -> Filter | None:
        if not filters:
            return None
        conditions = [
            FieldCondition(key=key, match=MatchValue(value=value))
            for key, value in filters.items()
        ]
        return Filter(must=conditions)


def build_default_connectors(profile: CompanyConfig) -> list[BaseResearchConnector]:
    """Build the list of connectors for a company, for live (internet) fetching."""
    available: dict[str, BaseResearchConnector] = {
        "edgar": EdgarConnector(),
        "newsweb": NewswebConnector(),
        "company_ir": CompanyIRConnector(),
        "yfinance": YFinanceConnector(),
        "web_search": WebSearchConnector(),
    }
    return [available[key] for key in profile.enabled_connectors if key in available]
