"""RAG repository and embedding search helpers."""
import logging
import time
from dataclasses import dataclass

import pg8000.dbapi

from app.config import Settings
from app.llm_client import LLMClient

pg8000.dbapi.paramstyle = "pyformat"  # named %(x)s parameters

log = logging.getLogger("rag")


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    heading: str
    chunk: str
    score: float


class DocumentRepository:
    def __init__(self, settings: Settings, llm: LLMClient):
        self._conn_args = dict(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            database=settings.db_name,
            timeout=10,
        )
        self.llm = llm

    def _connect(self):
        conn = pg8000.dbapi.connect(**self._conn_args)

        return conn

    def search(self, query: str, limit: int = 5, source_prefix: str | None = None) -> list[RetrievedChunk]:
        started = time.perf_counter()
        embedding, tokens = self.llm.embed_with_tokens(query)
        latency_ms = (time.perf_counter() - started) * 1000
        log.info(
            "event=embedding_completed query_chars=%d dimensions=%d tokens=%s latency_ms=%.3f",
            len(query),
            len(embedding),
            tokens,
            latency_ms,
        )

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT source, heading, chunk,
                       1 - (embedding <=> %(query_embedding)s::vector) AS score
                FROM documents
                WHERE %(prefix)s::text IS NULL OR source LIKE %(prefix)s::text || '%%'
                ORDER BY embedding <=> %(query_embedding)s::vector
                LIMIT %(limit)s
                """,
                {"query_embedding": vector_literal(embedding), "limit": limit, "prefix": source_prefix},
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        chunks = [
            RetrievedChunk(source=str(source), heading=str(heading), chunk=str(chunk), score=float(score))
            for source, heading, chunk, score in rows
        ]
        log.info(
            "event=rag_retrieved chunk_count=%d chunks=%s",
            len(chunks),
            [
                {"source": chunk.source, "heading": chunk.heading, "score": round(chunk.score, 6)}
                for chunk in chunks
            ],
        )
        return chunks


def vector_literal(values) -> str:
    """pgvector text form, cast with ::vector in SQL (pg8000's dbapi connection has no pgvector adapter)."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
