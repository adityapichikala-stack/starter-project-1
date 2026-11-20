"""Step 2 (code): required fields, quantities, deadline. Pure functions, no I/O."""
from datetime import date

from app.models import ExtractedOrder, ValidatedItem, ValidationResult


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def parse_deadline(text: str | None) -> date | None:
    """Raises ValueError if the text is present but not a real YYYY-MM-DD date."""
    text = _clean(text)
    if text is None:
        return None
    return date.fromisoformat(text)


def valid_quantity(q) -> bool:
    return isinstance(q, int) and not isinstance(q, bool) and q > 0


def validate(order: ExtractedOrder, email_date: date | None, lead_time_days: int) -> ValidationResult:
    issues: list[str] = []

    if order.refers_to_previous_order:
        issues.append("Refers to a previous order")
    if _clean(order.customer) is None:
        issues.append("Missing customer")
    if _clean(order.delivery_city) is None:
        issues.append("Missing delivery city")

    items: list[ValidatedItem] = []
    for it in order.items:
        name = _clean(it.product_name)
        if name is None:
            continue
        if valid_quantity(it.quantity):
            items.append(ValidatedItem(product_name=name, requested=it.quantity))
        else:
            raw = it.quantity_text if it.quantity_text is not None else it.quantity
            if raw is None:
                issues.append(f"Missing quantity for {name}")
            else:
                issues.append(f"Invalid quantity for {name}: '{raw}'")
            items.append(ValidatedItem(product_name=name, requested=None))
    if not items:
        issues.append("No items listed")

    deadline_str: str | None = None
    try:
        deadline = parse_deadline(order.deadline)
    except ValueError:
        deadline = None
        issues.append(f"Could not read deadline: '{order.deadline}'")
    if deadline is not None:
        deadline_str = deadline.isoformat()
        if email_date is not None and (deadline - email_date).days < lead_time_days:
            issues.append(
                f"Deadline {deadline_str} is less than {lead_time_days} days after the email date {email_date.isoformat()}"
            )

    return ValidationResult(deadline=deadline_str, items=items, issues=issues)
