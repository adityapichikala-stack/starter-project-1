"""Part of step 2 (code): check that what the LLM extracted is actually in the email.

The LLM is asked to copy values as written, so a hallucinated value shows up as text that
isn't in the email. Anything we can't find becomes an issue, so the order goes to Need info
and a person checks it, instead of the workflow acting on an invented value.
"""
import re
from datetime import date

from app.models import ExtractedOrder

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12, "a dozen": 12,
}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))


def normalize_text(s: str) -> str:
    """Lowercase, '&' -> 'and', drop punctuation, collapse spaces."""
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def appears_in(value: str, text: str) -> bool:
    v = normalize_text(value)
    return bool(v) and f" {v} " in f" {normalize_text(text)} "


def number_from_text(text: str) -> int | None:
    """'40' -> 40, 'forty' -> 40, 'twenty five' -> 25, 'a few' -> None."""
    t = normalize_text(text)
    m = re.search(r"\b(\d+)\b", t)
    if m:
        return int(m.group(1))
    total, found = 0, False
    for w in t.split():
        if w in NUMBER_WORDS:
            n = NUMBER_WORDS[w]
            total = total * n if n == 100 and total else total + n
            found = True
    return total if found else None


def explicit_date_parts(text: str) -> tuple[int | None, int, int] | None:
    """Pulls (year|None, month, day) from '24 November 2026', 'Nov 24', '2026-11-24'. None if no explicit date."""
    t = text.lower()
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})\.?,?(?:\s+(\d{{4}}))?\b", t)
    if m:
        return (int(m.group(3)) if m.group(3) else None), MONTHS[m.group(2)], int(m.group(1))
    m = re.search(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(\d{{4}}))?\b", t)
    if m:
        return (int(m.group(3)) if m.group(3) else None), MONTHS[m.group(1)], int(m.group(2))
    return None


def check_grounding(order: ExtractedOrder, email_text: str) -> list[str]:
    """Returns one issue per extracted value that the email doesn't support."""
    issues: list[str] = []

    if order.customer and not appears_in(order.customer, email_text):
        issues.append(f"Could not find customer '{order.customer}' in the email")
    if order.delivery_city and not appears_in(order.delivery_city, email_text):
        issues.append(f"Could not find delivery city '{order.delivery_city}' in the email")

    for it in order.items:
        name = " ".join(it.product_name.split())
        if name and not appears_in(name, email_text):
            issues.append(f"Could not find product '{name}' in the email")
        if it.quantity is None:
            continue
        if not it.quantity_text or not appears_in(it.quantity_text, email_text):
            issues.append(f"Could not find the quantity for {name} in the email")
        elif number_from_text(it.quantity_text) != it.quantity:
            issues.append(f"Quantity for {name} does not match the email: '{it.quantity_text}'")

    if order.deadline:
        if not order.deadline_text or not appears_in(order.deadline_text, email_text):
            issues.append(f"Could not find the deadline '{order.deadline}' in the email")
        else:
            parts = explicit_date_parts(order.deadline_text)
            try:
                d = date.fromisoformat(order.deadline.strip())
            except ValueError:
                d = None  # validate() reports unreadable deadlines
            if parts and d:
                year, month, day = parts
                if (month, day) != (d.month, d.day) or (year is not None and year != d.year):
                    issues.append(
                        f"Deadline {order.deadline} does not match the email: '{order.deadline_text}'")
    return issues
