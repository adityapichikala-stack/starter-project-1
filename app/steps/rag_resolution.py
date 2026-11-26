"""RAG-backed product matching. The model only filters code-supplied candidates; code validates its answer."""
import logging
import re

from app.llm_client import LLMClient
from app.models import ProductCodeMatches
from app.rag import RetrievedChunk

log = logging.getLogger("rag_resolution")

CODE_RE = re.compile(r"\b[A-Z]{3}-[A-Z0-9]+\b")
BROCHURE_PREFIX = "brochures/"

SYSTEM_PROMPT = (
    "You match a customer's product description to catalogue products using only the brochure excerpts. "
    "Return matching_codes: every candidate code whose brochure confirms EVERY detail in the description "
    "(colour, storage, 5G, size, wattage, ...). Closest is not a match; if a detail isn't confirmed by the "
    "brochure, exclude that product. Return an empty list if nothing qualifies. "
    "Valid codes are exactly: {codes}. Never invent a code."
)


def brochure_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    return [c for c in chunks if c.source.startswith(BROCHURE_PREFIX)]


def candidate_codes(chunks: list[RetrievedChunk], valid_codes: set[str]) -> list[str]:
    """Codes mentioned in the chunks that also exist in the products table, in first-seen order."""
    codes: list[str] = []
    for chunk in chunks:
        for code in CODE_RE.findall(chunk.chunk + "\n" + chunk.source):
            if code in valid_codes and code not in codes:
                codes.append(code)
    return codes


def matching_codes(
    llm: LLMClient,
    description: str,
    chunks: list[RetrievedChunk],
    valid_codes: set[str],
) -> list[str]:
    """Returns the final codes (0, 1 or more); the caller turns the count into resolved/ambiguous/unknown."""
    chunks = brochure_chunks(chunks)
    log.info("event=rag_query query=%r", description)
    for c in chunks:
        log.info("event=rag_chunk source=%s heading=%r score=%.4f", c.source, c.heading, c.score)
    candidates = candidate_codes(chunks, valid_codes)
    log.info("event=rag_candidates candidates=%s", candidates)
    if not candidates:
        log.info("event=rag_decision query=%r llm_answer=None final=[]", description)
        return []

    payload = "\n\n".join(f"Source: {c.source}\nHeading: {c.heading}\n{c.chunk}" for c in chunks)
    answer = llm.parse(
        "resolve_product",
        [
            {"role": "system", "content": SYSTEM_PROMPT.format(codes=", ".join(candidates))},
            {"role": "user", "content": f"Description: {description}\n\nBrochure excerpts:\n{payload}"},
        ],
        ProductCodeMatches,
    )
    final = [code for code in dict.fromkeys(answer.matching_codes) if code in candidates]
    log.info("event=rag_decision query=%r llm_answer=%s final=%s", description, answer.matching_codes, final)
    return final
