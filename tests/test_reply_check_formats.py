"""Valid ways of writing quote values must pass; anything not in result/quote/chunks must still fail."""
import pytest

from app.steps.reply_check import check_reply

RESULT = {"customer": "Deccan Fiber", "delivery_city": "Nashik", "deadline": "2026-12-10",
          "items": [{"product_name": "Meshwave AX3000 Router", "product_code": "RTR-AX3",
                     "requested": 12, "available": 30}], "status": "Ready", "issues": []}
QUOTE = {"lines": [{"product_code": "RTR-AX3", "quantity": 12, "unit_price_inr": 6499.0,
                    "discount_percent": 5, "line_total_inr": 74088.6}],
         "subtotal_inr": 74088.6, "delivery_inr": 0.0, "total_inr": 74088.6, "valid_until": "2026-11-27"}
CHUNKS = [{"source": "pricing_policy.md", "heading": "Delivery", "chunk": "Delivery takes 5 to 8 working days."}]
BASE = "We can supply 12 Meshwave AX3000 Router to Nashik by 10 December 2026. "


@pytest.mark.parametrize("phrase", [
    "The total is INR 74,088.60, including a 5% discount.",
    "The total is ₹74,088.60, including a 5% discount.",
    "The total is Rs. 74,088.6 including 5 percent discount.",
    "The total is 74088.60 INR.",
    "Unit price ₹6,499.00 x 12 = INR 74,088.60 after 5% off.",
    "Unit price ₹6,499 per router, delivery INR 0.00, total INR 74,088.60.",
    "Valid until 27 November 2026.",
    "Valid until 27th Nov 2026.",
    "Valid until November 27, 2026.",
    "Delivery takes 5-8 working days.",
])
def test_formatted_quote_values_are_accepted(phrase):
    assert check_reply(BASE + phrase, RESULT, QUOTE, CHUNKS) == [], phrase


@pytest.mark.parametrize("phrase", [
    "You get 15% off.",
    "We can offer a 15% discount.",
    "The total is INR 70,000.00.",
    "Unit price is ₹6,000.",
    "Valid until 30 November 2026.",
    "Delivery takes 2 working days.",
])
def test_invented_values_are_still_rejected(phrase):
    assert check_reply(BASE + phrase, RESULT, QUOTE, CHUNKS), phrase
