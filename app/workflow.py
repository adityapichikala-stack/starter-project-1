"""The fixed four-step workflow: extract (LLM, cross-checked, max N iterations) -> validate (code)
-> check stock (code) -> reply (LLM)."""
import logging
import re
import time
from decimal import Decimal

from app.email_parser import parse_email
from app.llm_client import LLMClient, LLMError
from app.models import OrderResult, ResolvedItem
from app.observability import OrderMetrics, measured_step, metrics_scope
from app.steps.extraction_loop import run_extraction
from app.steps.reply_check import check_reply
from app.steps.reply import draft_reply, fallback_reply
from app.steps.status import decide_status
from app.steps.stock import CatalogueLookup, check_stock
from app.steps.validate import validate
from app.steps.quote import build_quote
from app.steps.resolve_products import CatalogueProduct, resolve_validated_items
from app.steps.rag_resolution import matching_codes

log = logging.getLogger("workflow")


def _dedupe_chunks(chunks):
    seen, out = set(), []
    for c in chunks:
        key = (c.source, c.heading, c.chunk)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def customer_questions(body: str) -> str:
    """The question sentences of the email, used only as a retrieval query (never sent to the reply LLM)."""
    sentences = re.split(r"(?<=[.?!])\s+|\n+", body)
    return " ".join(s.strip() for s in sentences if s.strip().endswith("?"))


class OrderWorkflow:
    def __init__(self, llm: LLMClient, catalogue: CatalogueLookup, lead_time_days: int,
                 max_extraction_iterations: int = 5, input_cost_per_million: float | None = None,
                 output_cost_per_million: float | None = None, document_repository=None,
                 free_delivery_threshold: Decimal = Decimal("50000"),
                 delivery_charge: Decimal = Decimal("1500"),
                 quote_validity_days: int = 7,
                 discount_tiers: tuple[tuple[int, int], ...] = ((200, 12), (50, 8), (10, 5), (1, 0))):
        self.llm = llm
        self.catalogue = catalogue
        self.lead_time_days = lead_time_days
        self.max_extraction_iterations = max_extraction_iterations
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.document_repository = document_repository
        self.free_delivery_threshold = free_delivery_threshold
        self.delivery_charge = delivery_charge
        self.quote_validity_days = quote_validity_days
        self.discount_tiers = discount_tiers

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
                    full_catalogue = {}
                    if hasattr(self.catalogue, "get_catalogue"):
                        entries = self.catalogue.get_catalogue()
                        full_catalogue = {
                            key: CatalogueProduct(
                                product_code=entry.product_code,
                                name=entry.name,
                                unit_price_inr=entry.unit_price_inr or Decimal("0"),
                                quantity_available=entry.quantity_available,
                            )
                            for key, entry in entries.items()
                        }

                rag_candidates = {}
                chunks = []
                with measured_step("rag_resolution"):
                    for item in v.items:
                        key = item.product_name.lower().strip()
                        exact = full_catalogue.get(key)
                        if exact or not self.document_repository:
                            continue
                        found = self.document_repository.search(item.product_name, 5, "brochures/")
                        chunks.extend(found)
                        rag_candidates[item.product_name] = matching_codes(
                            self.llm, item.product_name, found,
                            {p.product_code for p in full_catalogue.values()},
                        )

                    chunks = _dedupe_chunks(chunks)
                    if self.document_repository:
                        questions = customer_questions(parsed.body)
                        if questions:
                            policy = self.document_repository.search(questions, 3, "pricing_policy.md")
                            chunks = _dedupe_chunks(chunks + policy[:3])

                    history_rows = []
                    if extracted.refers_to_previous_order and hasattr(self.catalogue, "find_history"):
                        history_rows = self.catalogue.find_history(extracted.customer or "")

                    resolution = resolve_validated_items(
                        v.items,
                        extracted.customer,
                        extracted.refers_to_previous_order,
                        full_catalogue,
                        history_rows,
                        rag_candidates,
                    )
                    if not full_catalogue:
                        resolution.items = []
                        resolution.issues = []
                        for item in v.items:
                            entry = catalogue.get(item.product_name.lower().strip())
                            if entry:
                                resolution.items.append(
                                    ResolvedItem(
                                        product_name=item.product_name,
                                        product_code=entry.product_code,
                                        requested=item.requested,
                                        available=entry.quantity_available,
                                        unit_price_inr=entry.unit_price_inr,
                                        resolution="exact",
                                    )
                                )

                with measured_step("quote_build"):
                    quote = build_quote(
                        resolution.items,
                        parsed.sent_date,
                        Decimal(str(self.free_delivery_threshold)),
                        Decimal(str(self.delivery_charge)),
                        self.quote_validity_days,
                        self.discount_tiers,
                    )

                # Status (code)
                with measured_step("status"):
                    if full_catalogue:
                        need_info = outcome.unresolved + v.issues + resolution.issues
                    else:
                        need_info = outcome.unresolved + v.issues + stock.unknown_issues + resolution.issues
                    resolved_shortages = []
                    resolved_items_out = []
                    for item in resolution.items:
                        if item.product_code is None:
                            resolved_items_out.append({
                                "product_name": item.product_name,
                                "product_code": None,
                                "requested": item.requested,
                                "available": None,
                            })
                            continue
                        available = item.available or 0
                        resolved_items_out.append({
                            "product_name": item.product_name,
                            "product_code": item.product_code,
                            "requested": item.requested,
                            "available": available,
                        })
                        if item.requested is not None and item.requested > available:
                            resolved_shortages.append(
                                f"{item.product_name}: requested {item.requested}, only {available} in stock"
                            )
                    status = decide_status(need_info, resolved_shortages)
                    result = {
                        "customer": " ".join(extracted.customer.split()) if extracted.customer else None,
                        "delivery_city": " ".join(extracted.delivery_city.split()) if extracted.delivery_city else None,
                        "deadline": v.deadline,
                        "items": resolved_items_out if resolution.items else [i.model_dump() for i in stock.items],
                        "status": status,
                        "issues": need_info + (resolved_shortages if resolution.items else stock.shortage_issues),
                    }

                    if status == "Need info" or not quote:
                        quote = None

                # 4. Draft reply from the computed result only; replace rejected drafts with a template.
                reply_chunks = [
                    {"source": c.source, "heading": c.heading, "chunk": c.chunk} for c in chunks
                ]
                reply = None
                for attempt in (1, 2):
                    try:
                        with measured_step("reply_generation"):
                            draft = draft_reply(
                                self.llm,
                                result,
                                parsed.sender_name or None,
                                quote.model_dump(mode="json") if quote else None,
                                reply_chunks,
                            )
                    except LLMError:
                        break
                    with measured_step("reply_check"):
                        problems = check_reply(
                            draft,
                            result,
                            quote.model_dump(mode="json") if quote else None,
                            reply_chunks,
                        )
                    if not problems:
                        reply = draft
                        break
                    metrics.reply_check_failures += 1
                    metrics.record_error("reply_check")
                    log.warning(
                        "event=reply_rejected request_id=%s check_attempt=%d error_category=reply_check problems=%s",
                        metrics.request_id,
                        attempt,
                        problems,
                    )
                if reply is None:
                    metrics.fallback_reply_used = True
                    log.warning(
                        "event=fallback_reply request_id=%s outcome=used error_category=reply_unavailable_or_rejected",
                        metrics.request_id,
                    )
                    reply = fallback_reply(result, parsed.sender_name or None,
                                           quote.model_dump(mode="json") if quote else None)

                response = OrderResult(**result, reply=reply, quote=quote)
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
