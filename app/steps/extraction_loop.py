"""Code-controlled extract -> check -> re-extract loop.

Each iteration: the LLM extracts (step 1), code runs the grounding check, and a second LLM call
cross-checks the values (step 1b). If anything fails, the problems are fed back and the LLM re-reads
the email. Code decides when to stop:
  - everything checks out, or
  - the new extraction is identical to the previous one (no progress, more tries won't help), or
  - max_iterations is reached (EXTRACTION_MAX_ITERATIONS, default 5).
Problems still open at the end become issues, which makes the order Need info.
"""
import logging
from dataclasses import dataclass, field

from app.email_parser import ParsedEmail
from app.llm_client import LLMClient
from app.models import ExtractedOrder, Verdict, Verification
from app.steps.extract import extract_order
from app.steps.grounding import appears_in, check_grounding
from app.steps.verify import verify_extraction

log = logging.getLogger("extraction_loop")


@dataclass
class ExtractionOutcome:
    order: ExtractedOrder
    iterations: int
    stop_reason: str  # "verified" | "no_progress" | "max_iterations"
    unresolved: list[str] = field(default_factory=list)  # become issues -> Need info


def verification_problems(v: Verification, order: ExtractedOrder, email_text: str) -> list[str]:
    """Turns the verifier's verdicts into problem strings. Code also checks the verifier's quotes,
    so a verifier that 'confirms' a value with an invented quote doesn't count."""
    problems: list[str] = []

    def check(label: str, verdict: Verdict, value) -> None:
        if not verdict.correct:
            problems.append(f"{label}: {verdict.problem or 'reviewer says the value is wrong'}")
        elif value not in (None, False) and verdict.evidence and not appears_in(verdict.evidence, email_text):
            problems.append(f"{label}: reviewer's supporting quote is not in the email")

    check("customer", v.customer, order.customer)
    check("delivery city", v.delivery_city, order.delivery_city)
    check("deadline", v.deadline, order.deadline)
    check("previous order", v.refers_to_previous_order, order.refers_to_previous_order)
    for item, verdict in zip(order.items, v.items):
        check(f"item '{item.product_name}'", verdict, item.quantity)
    if len(v.items) < len(order.items):
        problems.append("reviewer did not check every item")
    if v.missing_items:
        problems.append("products in the email but not extracted: " + ", ".join(v.missing_items))
    return problems


def run_extraction(llm: LLMClient, parsed: ParsedEmail, max_iterations: int) -> ExtractionOutcome:
    max_iterations = max(1, max_iterations)
    text = parsed.searchable_text
    previous: ExtractedOrder | None = None
    feedback: list[str] = []

    for i in range(1, max_iterations + 1):
        order = extract_order(llm, parsed, previous, feedback)

        if previous is not None and order == previous:
            log.warning("iteration=%d no progress, stopping. open problems=%s", i, feedback)
            return ExtractionOutcome(order, i, "no_progress", _as_issues(feedback))

        grounding = check_grounding(order, text)
        verification = verify_extraction(llm, parsed, order)
        problems = grounding + verification_problems(verification, order, text)
        log.info("iteration=%d grounding=%s verifier=%s", i, grounding, problems[len(grounding):])

        if not problems:
            return ExtractionOutcome(order, i, "verified")
        previous, feedback = order, problems

    log.warning("max_iterations=%d reached. open problems=%s", max_iterations, feedback)
    return ExtractionOutcome(order, max_iterations, "max_iterations", _as_issues(feedback))


def _as_issues(problems: list[str]) -> list[str]:
    return [p if p.startswith("Could not") or "does not match" in p else f"Could not verify {p}" for p in problems]
