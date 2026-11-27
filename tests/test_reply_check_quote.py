from app.steps.reply import fallback_reply
from app.steps.reply_check import check_reply

RESULT = {"customer": "Callpoint Services", "delivery_city": "Thiruvananthapuram", "deadline": "2026-12-07",
          "items": [{"product_name": "Quietline Headset", "product_code": "HDS-10", "requested": 4, "available": 45}],
          "status": "Ready", "issues": []}
QUOTE = {"lines": [{"product_code": "HDS-10", "quantity": 4, "unit_price_inr": 2299.0, "discount_percent": 5,
                    "line_total_inr": 8736.2}],
         "subtotal_inr": 8736.2, "delivery_inr": 1500.0, "total_inr": 10236.2, "valid_until": "2026-11-28"}
GOOD = ("We can supply 4 Quietline Headset to Thiruvananthapuram by 7 December 2026. Quote in INR: "
        "unit price 2,299.00 with 5% discount, subtotal 8,736.20, delivery 1,500.00, total 10,236.20. "
        "Valid until 28 November 2026.")


def test_valid_quote_reply_passes():
    assert check_reply(GOOD, RESULT, QUOTE) == []


def test_bigger_discount_fails():
    assert check_reply(GOOD.replace("5% discount", "15% discount"), RESULT, QUOTE)


def test_number_not_in_quote_fails():
    assert check_reply(GOOD.replace("8,736.20", "8,000.00"), RESULT, QUOTE)


def test_date_not_matching_fails():
    assert check_reply(GOOD.replace("28 November 2026", "30 November 2026"), RESULT, QUOTE)


def test_any_price_fails_when_quote_is_null():
    assert check_reply("The price is INR 2,299.", RESULT, None)
    assert check_reply("We can supply 4 Quietline Headset to Thiruvananthapuram by 7 December 2026.", RESULT, None) == []


def test_number_from_retrieved_chunk_allowed():
    chunks = [{"source": "brochures/HDS-10.md", "heading": "Warranty", "chunk": "Covered for 24 months."}]
    assert check_reply(GOOD + " Warranty is 24 months.", RESULT, QUOTE, chunks) == []
    assert check_reply(GOOD + " Warranty is 36 months.", RESULT, QUOTE, chunks)


def test_fallback_lists_quote():
    text = fallback_reply(RESULT, "Manoj", QUOTE)
    for expected in ("HDS-10", "2,299.00", "5%", "8,736.20", "1,500.00", "10,236.20", "2026-11-28"):
        assert expected in text


def test_policy_percentage_from_chunks_allowed_but_not_as_discount():
    chunks = [{"source": "pricing_policy.md", "heading": "Payment", "chunk": "50% in advance, rest on delivery."}]
    assert check_reply(GOOD + " Payment is 50% in advance.", RESULT, QUOTE, chunks) == []
    assert check_reply(GOOD + " We can give a 50% discount.", RESULT, QUOTE, chunks)


# ---- regression: email_27 (router + warranty/delivery questions) was sent to the fallback template ----
EMAIL_27_RESULT = {"customer": "Deccan Fiber", "delivery_city": "Nashik", "deadline": "2026-12-10",
                   "items": [{"product_name": "Meshwave AX3000 Router", "product_code": "RTR-AX3",
                              "requested": 12, "available": 30}], "status": "Ready", "issues": []}
EMAIL_27_QUOTE = {"lines": [{"product_code": "RTR-AX3", "quantity": 12, "unit_price_inr": 6499.0,
                             "discount_percent": 5, "line_total_inr": 74088.6}],
                  "subtotal_inr": 74088.6, "delivery_inr": 0.0, "total_inr": 74088.6, "valid_until": "2026-11-27"}
EMAIL_27_CHUNKS = [
    {"source": "pricing_policy.md", "heading": "Payment terms",
     "chunk": "50% in advance to confirm the order, and the rest on delivery."},
    {"source": "pricing_policy.md", "heading": "Delivery", "chunk": "Delivery takes 5 to 8 working days."},
    {"source": "pricing_policy.md", "heading": "Bulk discounts",
     "chunk": "| Quantity | Discount |\n| 10 to 49 | 5% |\n| 50 to 199 | 8% |"},
]
EMAIL_27_REPLY = (
    "We confirm that we can supply 12 units of Meshwave AX3000 Router as requested. The quote for your order is "
    "INR 74,088.6, including a 5% discount and free delivery. This quote is valid until 27 November 2026. Our team "
    "will arrange delivery to Nashik by 10 December 2026. Delivery to Nashik typically takes 5 to 8 working days "
    "after order confirmation. Payment terms are 50% in advance and the remainder on delivery."
)


def test_email_27_reply_is_accepted():
    assert check_reply(EMAIL_27_REPLY, EMAIL_27_RESULT, EMAIL_27_QUOTE, EMAIL_27_CHUNKS) == []


def test_policy_discount_tier_is_not_a_license_to_offer_it():
    # 8% appears in the policy table, but it is not this quote's discount, so it cannot be offered.
    reply = EMAIL_27_REPLY + " Our tier for 50 to 199 units is an 8% discount."
    assert check_reply(reply, EMAIL_27_RESULT, EMAIL_27_QUOTE, EMAIL_27_CHUNKS)


def test_invented_discount_still_fails_even_with_policy_chunks():
    for bad in ("We can give you a 15% discount.", "We can give you a 50% discount.", "Enjoy 20% off."):
        assert check_reply(EMAIL_27_REPLY + " " + bad, EMAIL_27_RESULT, EMAIL_27_QUOTE, EMAIL_27_CHUNKS), bad


# ---- the real pricing policy: its discount table must not let invented discounts through ----
from pathlib import Path

from scripts.ingest_docs import chunk_markdown

POLICY = Path(__file__).resolve().parents[1] / "starter-project-2-data" / "docs" / "pricing_policy.md"
POLICY_CHUNKS = [{"source": "pricing_policy.md", "heading": h, "chunk": body} for h, body in chunk_markdown(POLICY)]
TAB_RESULT = {"customer": "Greenfield Public School", "delivery_city": "Nagpur", "deadline": "2026-12-14",
              "items": [{"product_name": "Nimbus Tab 8 Lite", "product_code": "TAB-08", "requested": 20,
                         "available": 60}], "status": "Ready", "issues": []}
TAB_QUOTE = {"lines": [{"product_code": "TAB-08", "quantity": 20, "unit_price_inr": 8999.0, "discount_percent": 5,
                        "line_total_inr": 170981.0}],
             "subtotal_inr": 170981.0, "delivery_inr": 0.0, "total_inr": 170981.0, "valid_until": "2026-11-28"}
TAB_BASE = "We can supply 20 Nimbus Tab 8 Lite to Nagpur by 14 December 2026. "


def _tab(text):
    return check_reply(TAB_BASE + text, TAB_RESULT, TAB_QUOTE, POLICY_CHUNKS)


def test_real_policy_chunk_contains_the_bulk_discount_table():
    assert any("12%" in c["chunk"] and "8%" in c["chunk"] for c in POLICY_CHUNKS)


import pytest


@pytest.mark.parametrize("phrase", [
    "Good news, we can give you a 12% discount on this order.",
    "As a special favour we'll apply 8% off your tablets.",
    "We can offer 12% if you confirm today.",
    "We will reduce the price by 8%.",
])
def test_discount_not_in_quote_fails_even_when_in_policy_table(phrase):
    assert _tab(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "Your quote includes a 5% discount.",
    "50% is payable in advance.",
    "Payment terms: 50% in advance; we offer free delivery on this order.",
])
def test_quote_discount_and_non_discount_percentages_pass(phrase):
    assert _tab(phrase) == [], phrase
