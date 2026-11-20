"""The fixed four-step workflow: extract (LLM, cross-checked, max N iterations) -> validate (code)
-> check stock (code) -> reply (LLM)."""
import logging
import time

from app.email_parser import parse_email
from app.llm_client import LLMClient, LLMError
from app.models import OrderResult
from app.steps.extraction_loop import run_extraction
from app.steps.reply_check import check_reply
from app.steps.reply import draft_reply, fallback_reply
from app.steps.status import decide_status
from app.steps.stock import CatalogueLookup, check_stock
from app.steps.validate import validate

log = logging.getLogger("workflow")


class OrderWorkflow:
    def __init__(self, llm: LLMClient, catalogue: CatalogueLookup, lead_time_days: int,
                 max_extraction_iterations: int = 5):
        self.llm = llm
        self.catalogue = catalogue
        self.lead_time_days = lead_time_days
        self.max_extraction_iterations = max_extraction_iterations

    def run(self, raw_email: str) -> OrderResult:
        t0 = time.perf_counter()
        parsed = parse_email(raw_email)
        log.info("parse from=%s date=%s subject=%r", parsed.sender_address, parsed.sent_date, parsed.subject)

        # 1. Extract (LLM) + cross-check (code grounding + LLM verifier), re-extracting with feedback.
        #    Code controls the loop and caps it at max_extraction_iterations. LLMError propagates.
        outcome = run_extraction(self.llm, parsed, self.max_extraction_iterations)
        extracted = outcome.order
        log.info("extract iterations=%d stop=%s result=%s unresolved=%s", outcome.iterations,
                 outcome.stop_reason, extracted.model_dump_json(), outcome.unresolved)

        # 2. Validate (code)
        v = validate(extracted, parsed.sent_date, self.lead_time_days)
        log.info("validate deadline=%s items=%d issues=%s", v.deadline, len(v.items), v.issues)

        # 3. Check stock (code, read-only)
        catalogue = self.catalogue.find_by_names([i.product_name for i in v.items])
        stock = check_stock(v.items, catalogue)
        log.info("stock unknown=%s shortages=%s", stock.unknown_issues, stock.shortage_issues)

        # Status (code)
        need_info = outcome.unresolved + v.issues + stock.unknown_issues
        status = decide_status(need_info, stock.shortage_issues)
        result = {
            "customer": " ".join(extracted.customer.split()) if extracted.customer else None,
            "delivery_city": " ".join(extracted.delivery_city.split()) if extracted.delivery_city else None,
            "deadline": v.deadline,
            "items": [i.model_dump() for i in stock.items],
            "status": status,
            "issues": need_info + stock.shortage_issues,
        }
        log.info("status=%s issues=%s", status, result["issues"])

        # 4. Draft reply (LLM) from the result only. Code checks it; a reply that states anything the
        #    result doesn't contain is redrafted once, then replaced by a template.
        reply = None
        for attempt in (1, 2):
            try:
                draft = draft_reply(self.llm, result, parsed.sender_name or None)
            except LLMError as e:
                log.error("reply failed: %s", e)
                break
            problems = check_reply(draft, result)
            if not problems:
                reply = draft
                break
            log.warning("reply attempt=%d rejected: %s", attempt, problems)
        if reply is None:
            log.warning("using template reply")
            reply = fallback_reply(result, parsed.sender_name or None)

        log.info("done status=%s total_ms=%.0f", status, (time.perf_counter() - t0) * 1000)
        return OrderResult(**result, reply=reply)
