"""Build SP2 quotes with Decimal arithmetic. The LLM does not calculate any quote field."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models import Quote, QuoteLine, ResolvedItem

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def bulk_discount_percent(
    quantity: int,
    tiers: tuple[tuple[int, int], ...] = ((200, 12), (50, 8), (10, 5), (1, 0)),
) -> int:
    for threshold, discount in sorted(tiers, reverse=True):
        if quantity >= threshold:
            return discount
    return 0


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def build_quote(
    items: list[ResolvedItem],
    email_date: date | None,
    free_delivery_threshold: Decimal = Decimal("50000"),
    delivery_charge: Decimal = Decimal("1500"),
    validity_days: int = 7,
    discount_tiers: tuple[tuple[int, int], ...] = ((200, 12), (50, 8), (10, 5), (1, 0)),
) -> Quote | None:
    lines: list[QuoteLine] = []
    for item in items:
        if item.product_code is None or item.available is None or item.available <= 0:
            continue
        if item.requested is None:
            continue
        quantity = min(item.requested, item.available)
        if quantity <= 0 or item.unit_price_inr is None:
            continue

        unit_price = money(item.unit_price_inr)
        discount_percent = bulk_discount_percent(quantity, discount_tiers)
        discount = Decimal(discount_percent) / Decimal("100")
        line_total = money(unit_price * Decimal(quantity) * (Decimal("1") - discount))
        lines.append(
            QuoteLine(
                product_code=item.product_code,
                quantity=quantity,
                unit_price_inr=unit_price,
                discount_percent=discount_percent,
                line_total_inr=line_total,
            )
        )

    if not lines:
        return None

    subtotal = money(sum((line.line_total_inr for line in lines), ZERO))
    delivery = ZERO if subtotal >= money(free_delivery_threshold) else money(delivery_charge)
    total = money(subtotal + delivery)
    valid_until = (email_date + timedelta(days=validity_days)).isoformat() if email_date else None
    if valid_until is None:
        return None

    return Quote(
        lines=lines,
        subtotal_inr=subtotal,
        delivery_inr=delivery,
        total_inr=total,
        valid_until=valid_until,
    )
