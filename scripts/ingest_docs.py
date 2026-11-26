"""Idempotent SP2 brochure and policy embedding ingestion."""
from pathlib import Path
from typing import Iterator

import pg8000.dbapi
from pgvector.pg8000 import register_vector
from pgvector import Vector

from app.config import get_settings
from app.llm_client import LLMClient


def chunk_markdown(path: Path) -> Iterator[tuple[str, str]]:
    heading = "(untitled)"
    parts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if parts:
                yield heading, "\n".join(parts).strip()
            heading = line[3:].strip()
            parts = []
        else:
            parts.append(line)
    if parts:
        yield heading, "\n".join(parts).strip()


def main() -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    conn_args = dict(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        timeout=10,
    )
    conn = pg8000.dbapi.connect(**conn_args)
    register_vector(conn)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM documents")
        docs = Path(__file__).resolve().parents[1] / "docs"
        count = 0
        for path in sorted(docs.rglob("*.md")):
            source = path.relative_to(docs).as_posix()
            for heading, chunk in chunk_markdown(path):
                if not chunk:
                    continue
                embedding = llm.embed(chunk)
                cur.execute(
                    """
                    INSERT INTO documents (source, heading, chunk, embedding)
                    VALUES (%(source)s, %(heading)s, %(chunk)s, %(embedding)s::vector)
                    """,
                    {"source": source, "heading": heading, "chunk": chunk, "embedding": Vector(embedding)},
                )
                count += 1
        conn.commit()
        print(f"Ingested {count} document chunks.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
