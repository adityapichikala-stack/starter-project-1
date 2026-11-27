"""Idempotent SP2 brochure and policy embedding ingestion.

Every chunk is stored (and embedded) with a context prefix:
  brochures:           "Product: <name> (<code>) — <heading>"
  pricing_policy.md:   "Pricing policy — <heading>"
Run twice safely: the documents table is emptied and refilled in one transaction.
"""
import os
import re
import sys
from pathlib import Path
from typing import Iterator

import pg8000.dbapi

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `python scripts/ingest_docs.py` finds `app`
from app.config import get_settings
from app.llm_client import LLMClient
from app.rag import vector_literal

pg8000.dbapi.paramstyle = "pyformat"  # named %(x)s parameters

SP2_DATA_DIR = Path(os.getenv("SP2_DATA_DIR") or Path(__file__).resolve().parents[1] / "starter-project-2-data")
DOCS_DIR = SP2_DATA_DIR / "docs"
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 64
CREATE_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id        BIGSERIAL PRIMARY KEY,
    source    TEXT NOT NULL,
    heading   TEXT NOT NULL,
    chunk     TEXT NOT NULL,
    embedding vector(1536) NOT NULL
)
"""


def chunk_markdown(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (heading, text) per '## ' section; text before the first '## ' is the 'Introduction'."""
    heading = "Introduction"
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


def markdown_title(path: Path) -> str | None:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def prefix_for(path: Path, source: str, heading: str, product_names: dict[str, str]) -> str:
    """The context line put at the start of every chunk's text."""
    if source.startswith("brochures/"):
        code = path.stem
        name = product_names.get(code) or markdown_title(path) or code
        return f"Product: {name} ({code}) — {heading}"
    if path.name == "pricing_policy.md":
        return f"Pricing policy — {heading}"
    return f"{path.stem} — {heading}"


def build_chunks(docs: Path, product_names: dict[str, str]) -> list[tuple[str, str, str]]:
    """Returns (source, heading, prefixed_text) for every non-empty chunk, files in sorted order."""
    out = []
    for path in sorted(docs.rglob("*.md")):
        source = path.relative_to(docs).as_posix()
        for heading, body in chunk_markdown(path):
            if body:
                out.append((source, heading, f"{prefix_for(path, source, heading, product_names)}\n\n{body}"))
    return out


def check_dimensions(embeddings: list[list[float]], labels: list[str]) -> None:
    for vector, label in zip(embeddings, labels):
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise SystemExit(
                f"Embedding for {label} has {len(vector)} dimensions, expected {EMBEDDING_DIMENSIONS}. "
                "Check AZURE_OPENAI_EMBEDDING_DEPLOYMENT; nothing was written."
            )


def main() -> None:
    settings = get_settings()
    llm = LLMClient(settings)
    conn = pg8000.dbapi.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        timeout=10,
    )
    try:
        cur = conn.cursor()
        cur.execute("SELECT product_code, name FROM products")
        product_names = {code: name for code, name in cur.fetchall()}

        chunks = build_chunks(DOCS_DIR, product_names)
        if not chunks:
            raise SystemExit(f"No markdown chunks found under {DOCS_DIR}")

        embeddings: list[list[float]] = []
        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i:i + BATCH_SIZE]
            vectors = llm.embed_many([text for _, _, text in batch])
            check_dimensions(vectors, [f"{s} / {h}" for s, h, _ in batch])
            embeddings.extend(vectors)

        cur.execute(CREATE_DOCUMENTS)  # sp2_seed.sql only creates the extension and order_history
        cur.execute("DELETE FROM documents")
        per_file: dict[str, int] = {}
        for (source, heading, text), vector in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO documents (source, heading, chunk, embedding)
                VALUES (%(source)s, %(heading)s, %(chunk)s, %(embedding)s::vector)
                """,
                {"source": source, "heading": heading, "chunk": text, "embedding": vector_literal(vector)},
            )
            per_file[source] = per_file.get(source, 0) + 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

    for source, n in per_file.items():
        print(f"{source}: {n} chunks")
    print(f"Total: {len(chunks)} chunks in {len(per_file)} files.")


if __name__ == "__main__":
    main()
