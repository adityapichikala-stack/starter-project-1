"""Exact-name items get no RAG search, but their brochure chunks must still reach the reply and the reply check."""
from app.models import CatalogueEntry, ExtractedItem, ExtractedOrder
from app.rag import RetrievedChunk
from app.steps.stock import normalize_name
from app.workflow import OrderWorkflow
from tests.test_workflow import FakeLLM

EMAIL = """From: Sneha Joshi <sneha@deccanfiber.example>
Date: Fri, 20 Nov 2026 17:30:00 +0530
Subject: Routers

We'd like 12 Meshwave AX3000 Router. Delivery to Deccan Fiber, Nashik by 10 December 2026.
Sneha Joshi, Deccan Fiber
"""


class Catalogue:
    def find_by_names(self, names):
        return {}

    def get_catalogue(self):
        e = CatalogueEntry(product_code="RTR-AX3", name="Meshwave AX3000 Router", quantity_available=30,
                           unit_price_inr=6499)
        return {normalize_name(e.name): e}


class Docs:
    def __init__(self):
        self.searches, self.sources = [], []

    def search(self, query, limit=5, source_prefix=None):
        self.searches.append(query)
        return []

    def chunks_for_source(self, source):
        self.sources.append(source)
        return [RetrievedChunk(source, "Key specs", "Wi-Fi 6 with speeds up to 3000 Mbps.", 1.0)]


class ReplyLLM(FakeLLM):
    def __init__(self, reply):
        super().__init__(ExtractedOrder(
            customer="Deccan Fiber", delivery_city="Nashik", deadline="2026-12-10", deadline_text="10 December 2026",
            refers_to_previous_order=False,
            items=[ExtractedItem(product_name="Meshwave AX3000 Router", quantity=12, quantity_text="12")]))
        self.reply = reply

    def complete(self, step, messages, **_):
        self.reply_input = messages[-1]["content"]
        return self.reply


QUOTE_TEXT = ("We can supply 12 Meshwave AX3000 Router to Nashik by 10 December 2026. Quote in INR: subtotal 74,088.60 "
              "after a 5% discount, delivery 0.00, total 74,088.60, valid until 27 November 2026.")


def run(reply):
    docs = Docs()
    llm = ReplyLLM(reply)
    result = OrderWorkflow(llm, Catalogue(), 3, document_repository=docs).run(EMAIL)
    return result, docs, llm


def test_exact_match_item_gets_its_brochure_and_a_real_spec_passes():
    result, docs, llm = run(QUOTE_TEXT + " The router supports speeds up to 3000 Mbps.")
    assert docs.searches == [] and docs.sources == ["brochures/RTR-AX3.md"]
    assert "3000 Mbps" in llm.reply_input           # the brochure chunk reached the reply prompt
    assert result.status == "Ready" and result.reply.startswith("We can supply")   # not the fallback template


def test_made_up_spec_number_is_rejected_and_falls_back():
    result, _, _ = run(QUOTE_TEXT + " The router supports speeds up to 5400 Mbps.")
    assert result.reply.startswith("Dear ")         # fallback_reply template
