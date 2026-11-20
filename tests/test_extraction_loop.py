"""The code-controlled extract -> cross-check -> re-extract loop, with a scripted fake LLM."""
from app.email_parser import parse_email
from app.models import ExtractedItem, ExtractedOrder, Verdict, Verification
from app.steps.extraction_loop import run_extraction, verification_problems
from tests.helpers import all_correct

EMAIL = parse_email(
    "From: Thomas V <t@coast.example>\nDate: Fri, 13 Nov 2026 11:05:00 +0530\nSubject: routers\n\n"
    "Please send 20 Meshwave AX3000 Router units to our warehouse in Kakkanad, Kochi.\n"
    "Thomas Varghese\nCoastline Telecom")


def order(city="Kochi", qty=20, customer="Coastline Telecom"):
    return ExtractedOrder(customer=customer, delivery_city=city, deadline=None, deadline_text=None,
                          refers_to_previous_order=False,
                          items=[ExtractedItem(product_name="Meshwave AX3000 Router", quantity=qty,
                                               quantity_text=str(qty) if qty else None)])


def wrong_city(o):
    v = all_correct(o)
    v.delivery_city = Verdict(correct=False, evidence=None, problem="Kakkanad is an area; the city is Kochi")
    return v


class ScriptedLLM:
    """extractions: what each extract call returns, in order (last one repeats).
    judge: function(order) -> Verification."""
    def __init__(self, extractions, judge=all_correct):
        self.extractions, self.judge = list(extractions), judge
        self.calls, self.feedback_seen = [], []

    def parse(self, step, messages, schema, **_):
        self.calls.append(step)
        if schema is Verification:
            return self.judge(self.current)
        if len(messages) > 2:
            self.feedback_seen.append(messages[-1]["content"])
        n = sum(1 for c in self.calls if c == "extract")
        self.current = self.extractions[min(n, len(self.extractions)) - 1]
        return self.current


def test_clean_first_time_is_one_iteration():
    llm = ScriptedLLM([order()])
    out = run_extraction(llm, EMAIL, 5)
    assert (out.iterations, out.stop_reason, out.unresolved) == (1, "verified", [])
    assert llm.calls == ["extract", "verify"]


def test_verifier_catches_wrong_value_and_second_try_fixes_it():
    llm = ScriptedLLM([order(city="Kakkanad"), order(city="Kochi")],
                      judge=lambda o: wrong_city(o) if o.delivery_city == "Kakkanad" else all_correct(o))
    out = run_extraction(llm, EMAIL, 5)
    assert out.order.delivery_city == "Kochi"
    assert (out.iterations, out.stop_reason, out.unresolved) == (2, "verified", [])
    assert "Kakkanad is an area" in llm.feedback_seen[0]  # the problem was fed back to the extractor


def test_grounding_failure_triggers_recheck():
    llm = ScriptedLLM([order(city="Pune"), order(city="Kochi")])  # Pune isn't in the email
    out = run_extraction(llm, EMAIL, 5)
    assert out.order.delivery_city == "Kochi" and out.iterations == 2
    assert "Pune" in llm.feedback_seen[0]


def test_stops_at_max_iterations_never_loops_forever():
    flip = [order(qty=q) for q in (21, 22, 23, 24, 25, 26, 27, 28)]  # keeps changing, never grounded
    llm = ScriptedLLM(flip)
    out = run_extraction(llm, EMAIL, 5)
    assert (out.iterations, out.stop_reason) == (5, "max_iterations")
    assert llm.calls.count("extract") == 5
    assert out.unresolved  # still-open problems become issues -> Need info


def test_max_iterations_is_configurable():
    llm = ScriptedLLM([order(qty=q) for q in (21, 22, 23)])
    assert run_extraction(llm, EMAIL, 2).iterations == 2
    assert llm.calls.count("extract") == 2


def test_no_progress_stops_early():
    llm = ScriptedLLM([order(city="Kakkanad")], judge=wrong_city)  # model keeps giving the same answer
    out = run_extraction(llm, EMAIL, 5)
    assert (out.iterations, out.stop_reason) == (2, "no_progress")
    assert any("delivery city" in i for i in out.unresolved)


def test_verifier_quote_must_be_in_email():
    o = order()
    v = all_correct(o)
    v.customer = Verdict(correct=True, evidence="Coastline Telecom Pvt Ltd, Chennai", problem=None)
    assert any("quote is not in the email" in p for p in verification_problems(v, o, EMAIL.searchable_text))


def test_missing_items_reported_by_verifier():
    o = order()
    v = all_correct(o)
    v.missing_items = ["Volt 65W Charger"]
    assert any("Volt 65W Charger" in p for p in verification_problems(v, o, EMAIL.searchable_text))
