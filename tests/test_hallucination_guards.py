"""Code checks that catch LLM hallucinations: grounding of extracted values, and the reply check."""
import pytest

from app.email_parser import parse_email
from app.models import ExtractedItem, ExtractedOrder
from app.steps.grounding import check_grounding, explicit_date_parts, number_from_text
from app.steps.reply_check import check_reply

EMAIL = """Hi,
We need 40 units of Nimbus Tab 10 and forty PEBBLE   BUDS.
Deliver to Greenfield Public School, Dharampeth, Nagpur, no later than 30 November 2026.
Maple & Co Events"""


def order(**kw):
    base = dict(customer="Greenfield Public School", delivery_city="Nagpur", deadline="2026-11-30",
                deadline_text="no later than 30 November 2026", refers_to_previous_order=False,
                items=[ExtractedItem(product_name="Nimbus Tab 10", quantity=40, quantity_text="40"),
                       ExtractedItem(product_name="Pebble Buds", quantity=40, quantity_text="forty")])
    base.update(kw)
    return ExtractedOrder(**base)


def test_faithful_extraction_passes():
    assert check_grounding(order(), EMAIL) == []


def test_ampersand_and_punctuation_are_ignored():
    assert check_grounding(order(customer="Maple and Co Events"), EMAIL) == []


@pytest.mark.parametrize("field,value,expect", [
    ("customer", "Greenfield International School", "customer"),
    ("delivery_city", "Pune", "delivery city"),            # invented city
])
def test_invented_text_fields(field, value, expect):
    issues = check_grounding(order(**{field: value}), EMAIL)
    assert any(expect in i for i in issues)


def test_city_substring_of_other_word_is_not_enough():
    assert check_grounding(order(delivery_city="Nag"), EMAIL)


def test_invented_product():
    o = order(items=[ExtractedItem(product_name="Nimbus Tab 12", quantity=40, quantity_text="40")])
    assert any("product 'Nimbus Tab 12'" in i for i in check_grounding(o, EMAIL))


def test_quantity_not_matching_its_text():
    o = order(items=[ExtractedItem(product_name="Nimbus Tab 10", quantity=50, quantity_text="40")])
    assert any("does not match" in i for i in check_grounding(o, EMAIL))


def test_number_invented_from_vague_text():
    email = "We need a few Nimbus Tab 10 in Nagpur. Greenfield Public School"
    o = order(deadline=None, deadline_text=None,
              items=[ExtractedItem(product_name="Nimbus Tab 10", quantity=3, quantity_text="a few")])
    assert any("does not match" in i for i in check_grounding(o, email))


def test_quantity_without_evidence():
    o = order(items=[ExtractedItem(product_name="Nimbus Tab 10", quantity=40, quantity_text=None)])
    assert any("Could not find the quantity" in i for i in check_grounding(o, EMAIL))


def test_deadline_without_evidence():
    assert any("deadline" in i for i in check_grounding(order(deadline_text=None), EMAIL))


def test_deadline_date_mismatch():
    issues = check_grounding(order(deadline="2026-11-03"), EMAIL)
    assert any("does not match the email" in i for i in issues)


def test_relative_deadline_is_accepted_when_phrase_is_in_email():
    email = "Send 5 Pebble Buds to Nagpur, a week from today. Greenfield Public School"
    o = order(deadline="2026-11-23", deadline_text="a week from today",
              items=[ExtractedItem(product_name="Pebble Buds", quantity=5, quantity_text="5")])
    assert check_grounding(o, email) == []


@pytest.mark.parametrize("text,n", [("40", 40), ("40 units", 40), ("forty", 40), ("twenty five", 25),
                                    ("a dozen", 12), ("a few", None), ("some", None)])
def test_number_from_text(text, n):
    assert number_from_text(text) == n


@pytest.mark.parametrize("text,parts", [("24 November 2026", (2026, 11, 24)), ("Nov 24th", (None, 11, 24)),
                                        ("December 1, 2026", (2026, 12, 1)), ("2026-12-05", (2026, 12, 5)),
                                        ("Thursday 19 November", (None, 11, 19)), ("a week from today", None)])
def test_explicit_date_parts(text, parts):
    assert explicit_date_parts(text) == parts


# ---------- HTML / unstructured input ----------
def test_html_only_email_is_converted_to_text():
    raw = ("From: A <a@x.example>\nDate: Mon, 16 Nov 2026 10:00:00 +0530\nSubject: s\n"
           "Content-Type: text/html\n\n<html><body><p>Send <b>5 Pebble Buds</b></p><p>to Pune</p>"
           "<style>p{color:red}</style></body></html>")
    body = parse_email(raw).body
    assert "<" not in body and "color" not in body
    assert "5 Pebble Buds" in body and "Pune" in body


def test_multipart_html_only():
    raw = ("From: A <a@x.example>\nDate: Mon, 16 Nov 2026 10:00:00 +0530\nSubject: s\nMIME-Version: 1.0\n"
           'Content-Type: multipart/alternative; boundary="B"\n\n--B\nContent-Type: text/html\n\n'
           "<div>Send 5 Pebble Buds to Pune</div>\n--B--\n")
    assert parse_email(raw).body == "Send 5 Pebble Buds to Pune"


# ---------- reply check ----------
RESULT = {"customer": "Orbit Retail", "delivery_city": "Chennai", "deadline": "2026-12-01",
          "items": [{"product_name": "Astra X9", "product_code": "PHN-X9", "requested": 10, "available": 6}],
          "status": "Partial", "issues": ["Astra X9: requested 10, only 6 in stock"]}


def test_good_reply_passes():
    assert check_reply("You asked for 10 Astra X9; we have 6. Delivery to Chennai by 1 December 2026.", RESULT) == []


@pytest.mark.parametrize("reply", ["We confirm a 50% discount.", "The price is Rs 34,999.",
                                   "Total cost: ₹349990", "We offer a discount on this order."])
def test_reply_with_money_or_discount_is_rejected(reply):
    assert check_reply(reply, RESULT)


def test_reply_with_invented_number_is_rejected():
    assert check_reply("We can ship the remaining 4 next week.", RESULT)  # 4 isn't in the result
