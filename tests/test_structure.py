"""Enforces the workflow structure from the guide:
1. Extract = LLM, 2. Validate = code, 3. Check stock = code, 4. Reply = LLM. Status is decided by code."""
import ast
from pathlib import Path

from app.models import CatalogueEntry, ExtractedItem, ExtractedOrder, Verification
from tests.helpers import all_correct
from app.steps.stock import normalize_name
from app.workflow import OrderWorkflow

APP = Path(__file__).resolve().parent.parent / "app"
CODE_STEPS = ["steps/validate.py", "steps/grounding.py", "steps/stock.py", "steps/status.py", "steps/reply_check.py"]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_code_steps_never_use_the_llm():
    for f in CODE_STEPS:
        mods = imported_modules(APP / f)
        assert not any(m.startswith("openai") or m == "app.llm_client" for m in mods), f


def test_only_extract_and_reply_use_the_llm():
    users = {
        p.relative_to(APP).as_posix()
        for p in APP.rglob("*.py")
        if any(m.startswith("openai") or m == "app.llm_client" for m in imported_modules(p))
    }
    # llm_client wraps the SDK; extraction_loop/workflow/main only wire it up and handle LLMError
    assert users == {"llm_client.py", "steps/extract.py", "steps/verify.py", "steps/extraction_loop.py",
                     "steps/reply.py", "workflow.py", "main.py"}


def test_extraction_schema_cannot_set_status():
    assert "status" not in ExtractedOrder.model_fields
    assert set(ExtractedOrder.model_fields) == {
        "customer", "delivery_city", "deadline", "deadline_text", "refers_to_previous_order", "items"}


class RecordingLLM:
    def __init__(self):
        self.calls = []

    def parse(self, step, messages, schema, **_):
        self.calls.append(step)
        if schema is Verification:
            return all_correct(self.last)
        self.last = ExtractedOrder(customer="Acme Ltd", delivery_city="Pune", deadline=None, deadline_text=None,
                              refers_to_previous_order=False,
                              items=[ExtractedItem(product_name="Pebble Buds", quantity=5, quantity_text="5")])
        return self.last

    def complete(self, step, messages, **_):
        self.calls.append(step)
        self.reply_input = messages[-1]["content"]
        return "reply"


class RecordingCatalogue:
    def __init__(self, llm):
        self.llm = llm

    def find_by_names(self, names):
        self.llm.calls.append("stock_db")
        e = CatalogueEntry(product_code="EAR-01", name="Pebble Buds", quantity_available=350)
        return {normalize_name(e.name): e}


def test_steps_run_in_order_with_two_llm_calls():
    llm = RecordingLLM()
    r = OrderWorkflow(llm, RecordingCatalogue(llm), 3).run(
        "From: A <a@x.example>\nDate: Mon, 16 Nov 2026 10:00:00 +0530\nSubject: s\n\n"
        "Send 5 Pebble Buds to Pune. SECRET-RAW-TEXT\nAcme Ltd")
    assert llm.calls == ["extract", "verify", "stock_db", "reply"]  # LLM -> LLM check -> code (DB) -> LLM
    assert r.status == "Ready"
    assert "SECRET-RAW-TEXT" not in llm.reply_input  # reply LLM gets the result, not the email
