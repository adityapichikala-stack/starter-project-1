"""The fixed four-step workflow: extract (LLM, cross-checked, max N iterations) -> validate (code)
-> check stock (code) -> reply (LLM)."""
import logging
import time

from app.email_parser import parse_email
from app.llm_client import LLMClient, LLMError
from app.models import OrderResult
from app.observability import OrderMetrics, measured_step, metrics_scope
from app.steps.extraction_loop import run_extraction
from app.steps.reply_check import check_reply
from app.steps.reply import draft_reply, fallback_reply
from app.steps.status import decide_status
from app.steps.stock import CatalogueLookup, check_stock
from app.steps.validate import validate

log = logging.getLogger("workflow")


class OrderWorkflow:
    def __init__(self, llm: LLMClient, catalogue: CatalogueLookup, lead_time_days: int,
                 max_extraction_iterations: int = 5, input_cost_per_million: float | None = None,
                 output_cost_per_million: float | None = None):
        self.llm = llm
        self.catalogue = catalogue
        self.lead_time_days = lead_time_days
        self.max_extraction_iterations = max_extraction_iterations
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million

    def run(self, raw_email: str, metrics: OrderMetrics | None = None) -> OrderResult:
        metrics = metrics or OrderMetrics(self.input_cost_per_million, self.output_cost_per_million)
        t0 = time.perf_counter()
        with metrics_scope(metrics):
            completed = False
            try:
                with measured_step("email_parse"):
                    parsed = parse_email(raw_email)

                # 1. Extract (LLM) + cross-check (code grounding + LLM verifier).
                with measured_step("extraction"):
                    outcome = run_extraction(self.llm, parsed, self.max_extraction_iterations)
                extracted = outcome.order
                log.info(
                    "event=extraction_completed request_id=%s iterations=%d stop_reason=%s",
                    metrics.request_id,
                    outcome.iterations,
                    outcome.stop_reason,
                )

                # 2. Validate (code)
                with measured_step("validation"):
                    v = validate(extracted, parsed.sent_date, self.lead_time_days)

                # 3. Check stock (code, read-only)
                with measured_step("stock_lookup"):
                    catalogue = self.catalogue.find_by_names([i.product_name for i in v.items])
                    stock = check_stock(v.items, catalogue)

                # Status (code)
                with measured_step("status"):
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

                # 4. Draft reply from the computed result only; replace rejected drafts with a template.
                reply = None
                for attempt in (1, 2):
                    try:
                        with measured_step("reply_generation"):
                            draft = draft_reply(self.llm, result, parsed.sender_name or None)
                    except LLMError:
                        break
                    with measured_step("reply_check"):
                        problems = check_reply(draft, result)
                    if not problems:
                        reply = draft
                        break
                    metrics.reply_check_failures += 1
                    metrics.record_error("reply_check")
                    log.warning(
                        "event=reply_rejected request_id=%s check_attempt=%d error_category=reply_check",
                        metrics.request_id,
                        attempt,
                    )
                if reply is None:
                    metrics.fallback_reply_used = True
                    log.warning(
                        "event=fallback_reply request_id=%s outcome=used error_category=reply_unavailable_or_rejected",
                        metrics.request_id,
                    )
                    reply = fallback_reply(result, parsed.sender_name or None)

                response = OrderResult(**result, reply=reply)
                completed = True
                return response
            finally:
                total_ms = (time.perf_counter() - t0) * 1000
                metrics.total_duration_ms = total_ms
                metrics_summary = metrics.to_dict()
                log.info(
                    "event=workflow_completed request_id=%s outcome=%s duration_ms=%.3f "
                    "llm_calls=%d llm_attempts=%d llm_retries=%d prompt_tokens=%d "
                    "completion_tokens=%d extraction_iterations=%d fallback_reply=%s "
                    "estimated_cost=%s cost_status=%s error_counts=%s",
                    metrics.request_id,
                    "success" if completed else "failed",
                    total_ms,
                    metrics.llm_calls,
                    metrics.llm_attempts,
                    metrics.llm_retries,
                    metrics.prompt_tokens,
                    metrics.completion_tokens,
                    metrics.extraction_iterations,
                    metrics.fallback_reply_used,
                    metrics_summary["estimated_cost"]["value"],
                    metrics_summary["estimated_cost"]["status"],
                    metrics.error_counts,
                )
