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
