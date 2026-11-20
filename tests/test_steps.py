"""Tests for the code steps. No LLM and no database needed."""
from datetime import date

import pytest

from app.email_parser import parse_email
from app.models import CatalogueEntry, ExtractedItem, ExtractedOrder, ValidatedItem
from app.steps.status import decide_status
from app.steps.stock import check_stock, normalize_name
from app.steps.validate import validate

CATALOGUE = {
    normalize_name(e.name): e
    for e in [
        CatalogueEntry(product_code="TAB-10", name="Nimbus Tab 10", quantity_available=25),
        CatalogueEntry(product_code="EAR-01", name="Pebble Buds", quantity_available=350),
        CatalogueEntry(product_code="WCH-S2", name="Pulse Watch S2", quantity_available=0),
    ]
}


def order(**kw) -> ExtractedOrder:
    base = dict(customer="Acme Ltd", delivery_city="Pune", deadline=None, deadline_text=None,
                refers_to_previous_order=False,
                items=[ExtractedItem(product_name="Pebble Buds", quantity=5, quantity_text="5")])
    base.update(kw)
    return ExtractedOrder(**base)


# ---------- email parsing ----------
@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_parse_email_lf_and_crlf(nl):
    raw = nl.join(["From: A B <a@x.example>", "Date: Mon, 16 Nov 2026 10:12:00 +0530",
                   "Subject: Hi", "", "Body line", "second"])
    p = parse_email(raw)
    assert p.sent_date == date(2026, 11, 16)
    assert p.sender_name == "A B"
    assert p.body.splitlines()[0] == "Body line"


def test_parse_email_without_headers():
    p = parse_email("Hi,\nplease send 5 Pebble Buds")
    assert p.sent_date is None
    assert "Pebble Buds" in p.body


# ---------- validation ----------
def test_valid_order_has_no_issues():
    v = validate(order(deadline="2026-11-24"), date(2026, 11, 16), 3)
    assert v.issues == []
    assert v.deadline == "2026-11-24"


@pytest.mark.parametrize("field,issue", [("customer", "Missing customer"),
                                         ("delivery_city", "Missing delivery city")])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_required_fields(field, issue, value):
    v = validate(order(**{field: value}), date(2026, 11, 16), 3)
    assert issue in v.issues


def test_no_items():
    v = validate(order(items=[]), date(2026, 11, 16), 3)
    assert "No items listed" in v.issues


def test_previous_order():
    v = validate(order(items=[], refers_to_previous_order=True), date(2026, 11, 18), 3)
    assert v.issues == ["Refers to a previous order", "No items listed"]


@pytest.mark.parametrize("qty,text", [(None, "a few"), (0, "0"), (-2, "-2"), (None, None)])
def test_invalid_quantity(qty, text):
    v = validate(order(items=[ExtractedItem(product_name="Pebble Buds", quantity=qty, quantity_text=text)]),
                 date(2026, 11, 16), 3)
    assert v.items[0].requested is None
    assert any("quantity for Pebble Buds" in i for i in v.issues)


@pytest.mark.parametrize("deadline,ok", [("2026-11-19", True), ("2026-11-18", False),
                                         ("2026-11-16", False), ("2026-11-10", False)])
def test_deadline_lead_time(deadline, ok):
    v = validate(order(deadline=deadline), date(2026, 11, 16), 3)  # 19th is exactly 3 days later
    assert (v.issues == []) is ok


def test_lead_time_is_configurable():
    assert validate(order(deadline="2026-11-18"), date(2026, 11, 16), 2).issues == []


def test_unreadable_deadline():
    v = validate(order(deadline="4126-02-31"), date(2026, 11, 16), 3)
    assert v.deadline is None
    assert any("Could not read deadline" in i for i in v.issues)


def test_no_deadline_is_fine():
    assert validate(order(deadline=None), date(2026, 11, 16), 3).issues == []


# ---------- stock comparison ----------
def test_match_ignores_case_and_spaces():
    r = check_stock([ValidatedItem(product_name="  PEBBLE   buds ", requested=10)], CATALOGUE)
    assert r.items[0].product_code == "EAR-01"
    assert r.items[0].available == 350
    assert r.unknown_issues == r.shortage_issues == []


def test_similar_name_is_not_a_match():
    r = check_stock([ValidatedItem(product_name="Nimbus Tab 12", requested=30)], CATALOGUE)
    assert r.items[0].product_code is None and r.items[0].available is None
    assert r.unknown_issues == ["Unknown product: Nimbus Tab 12"]


def test_shortage():
    r = check_stock([ValidatedItem(product_name="Nimbus Tab 10", requested=40)], CATALOGUE)
    assert r.shortage_issues == ["Nimbus Tab 10: requested 40, only 25 in stock"]


def test_exact_stock_is_enough():
    assert check_stock([ValidatedItem(product_name="Nimbus Tab 10", requested=25)], CATALOGUE).shortage_issues == []


def test_zero_stock_is_short():
    r = check_stock([ValidatedItem(product_name="Pulse Watch S2", requested=1)], CATALOGUE)
    assert r.shortage_issues


def test_invalid_quantity_item_still_shows_available():
    r = check_stock([ValidatedItem(product_name="Pebble Buds", requested=None)], CATALOGUE)
    assert r.items[0].available == 350 and r.shortage_issues == []


# ---------- status ----------
@pytest.mark.parametrize("need,short,expected", [
    ([], [], "Ready"),
    ([], ["x short"], "Partial"),
    (["missing"], [], "Need info"),
    (["missing"], ["x short"], "Need info"),
])
def test_status_priority(need, short, expected):
    assert decide_status(need, short) == expected
