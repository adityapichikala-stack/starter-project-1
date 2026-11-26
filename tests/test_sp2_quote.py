from datetime import date
from decimal import Decimal

import pytest

from app.models import ResolvedItem
from app.steps.quote import bulk_discount_percent, build_quote


@pytest.mark.parametrize(
    ("quantity", "discount"),
    [(1, 0), (9, 0), (10, 5), (49, 5), (50, 8), (199, 8), (200, 12), (250, 12)],
)
def test_bulk_discount_boundaries(quantity, discount):
    assert bulk_discount_percent(quantity) == discount


@pytest.mark.parametrize(
    ("subtotal", "delivery"),
    [(Decimal("49999.99"), Decimal("1500.00")), (Decimal("50000.00"), Decimal("0.00"))],
)
def test_delivery_threshold(subtotal, delivery):
    item = ResolvedItem(
        product_name="Test",
        product_code="TST-01",
        requested=1,
        available=1,
        unit_price_inr=subtotal,
    )
    quote = build_quote([item], date(2026, 11, 20))
    assert quote.delivery_inr == delivery


def test_quote_rounds_to_paisa_and_valid_until_is_email_date_plus_seven():
    item = ResolvedItem(
        product_name="Test",
        product_code="TST-01",
        requested=10,
        available=10,
        unit_price_inr=Decimal("100.005"),
    )
    quote = build_quote([item], date(2026, 11, 20))
    assert quote.lines[0].unit_price_inr == Decimal("100.01")
    assert quote.lines[0].line_total_inr == Decimal("950.10")
    assert quote.subtotal_inr == Decimal("950.10")
    assert quote.valid_until == "2026-11-27"


def test_quote_quotes_only_stock_and_uses_quoted_quantity():
    items = [
        ResolvedItem(product_name="Short", product_code="SHT-01", requested=20, available=12,
                     unit_price_inr=Decimal("100")),
        ResolvedItem(product_name="Zero", product_code="ZRO-01", requested=5, available=0,
                     unit_price_inr=Decimal("100")),
    ]
    quote = build_quote(items, date(2026, 11, 20))
    assert [line.product_code for line in quote.lines] == ["SHT-01"]
    assert quote.lines[0].quantity == 12


def test_no_quotable_lines_or_missing_email_date_returns_null():
    item = ResolvedItem(product_name="Zero", product_code="ZRO-01", requested=5, available=0,
                        unit_price_inr=Decimal("100"))
    assert build_quote([item], date(2026, 11, 20)) is None
    valid = ResolvedItem(product_name="X", product_code="X", requested=1, available=1,
                         unit_price_inr=Decimal("100"))
    assert build_quote([valid], None) is None
