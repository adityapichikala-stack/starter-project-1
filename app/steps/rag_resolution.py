"""Small RAG-backed product-code chooser; code validates every model response."""
import re

from app.llm_client import LLMClient
from app.models import ProductCodeChoice
from app.rag import RetrievedChunk

CODE_RE = re.compile(r"\b[A-Z]{3}-[A-Z0-9]+\b")


def candidate_codes(chunks: list[RetrievedChunk]) -> list[str]:
    codes: list[str] = []
    for chunk in chunks:
        for code in CODE_RE.findall(chunk.chunk + "\n" + chunk.source):
            if code not in codes:
                codes.append(code)
    return codes


def choose_description_code(
    llm: LLMClient,
    description: str,
    chunks: list[RetrievedChunk],
) -> str | None:
    candidates = candidate_codes(chunks)
    payload = "\n\n".join(
        f"Source: {chunk.source}\nHeading: {chunk.heading}\n{chunk.chunk}"
        for chunk in chunks
    )
    choice = llm.parse(
        "resolve_product",
        [
            {
                "role": "system",
                "content": (
                    "Choose the catalogue product code for the description using only the retrieved chunks. "
                    f"Valid product_code values are exactly: {', '.join(candidates)}. "
                    "Return null if it is not a unique match. Never invent a code."
                ),
            },
            {"role": "user", "content": f"Description: {description}\n\nRetrieved chunks:\n{payload}"},
        ],
        ProductCodeChoice,
    )
    if choice.product_code not in candidates:
        return None
    return choice.product_code


def choose_description_candidates(
    llm: LLMClient,
    description: str,
    chunks: list[RetrievedChunk],
) -> list[str]:
    candidates = candidate_codes(chunks)
    if len(candidates) != 1:
        return candidates
    code = choose_description_code(llm, description, chunks)
    return [code] if code else []
