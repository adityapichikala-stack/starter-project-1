"""Code check on the LLM-written reply: it may only state what the computed result, the quote and the
retrieved chunks contain."""
import json
import re
from datetime import date
from decimal import Decimal

# Applied when there is NO quote: no money, discount or percentage talk at all.
FORBIDDEN = [
    (r"₹|\brs\.?\s*\d|\binr\b", "mentions money"),
    (r"\bprices?\b|\bpricing\b|\bcosts?\b|\binvoice\b", "mentions price/cost"),
    (r"\bdiscount", "mentions a discount"),
    (r"\d+\s*%|\bper\s*cent\b|\bpercent\b", "mentions a percentage"),
]

DISCOUNT_WORDS = re.compile(r"\b(?:discount|off|offer|appl|giv|reduc)", re.I)
CLAUSE_END = re.compile(r"[.;!?\n]")
NUMBER_RE =re.compile(r"\d+(?:,\d+)*(?:\.\d+)?")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s*cent\b|percent\b)", re.I)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
DATE_RE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})\b", re.I)


def _num(text: str) -> Decimal:
    return Decimal(text.replace(",", "")).normalize()


def _numbers(text: str) -> set[Decimal]:
    return {_num(n) for n in NUMBER_RE.findall(text)}


def _chunk_text(chunk) -> str:
    if isinstance(chunk, dict):
        return f"{chunk.get('heading', '')} {chunk.get('chunk', '')}"
    return f"{getattr(chunk, 'heading', '')} {getattr(chunk, 'chunk', '')}"


def _allowed_dates(result: dict, quote: dict | None) -> set[date]:
    out: set[date] = set()
    for value in (result.get("deadline"), (quote or {}).get("valid_until")):
        try:
            if value:
                out.add(date.fromisoformat(value))
        except ValueError:
            pass
    return out


def check_reply(reply: str, result: dict, quote: dict | None = None, chunks: list | None = None) -> list[str]:
    """Returns problems found; empty means the reply is OK to send."""
    problems: list[str] = []
    if quote is None:
        problems += [why for pattern, why in FORBIDDEN if re.search(pattern, reply, re.I)]

    # Dates: allowed only when they match the deadline or valid_until; they are then removed from the text.
    allowed_dates = _allowed_dates(result, quote)

    def _date(m: re.Match) -> str:
        try:
            d = date(int(m.group(3)), MONTHS.index(m.group(2).capitalize()) + 1, int(m.group(1)))
        except ValueError:
            return m.group(0)
        if d in allowed_dates:
            return " "
        problems.append(f"date {m.group(0)} is not the deadline or quote validity date")
        return " "

    text = DATE_RE.sub(_date, reply)

    allowed = _numbers(json.dumps(result))
    if quote is not None:
        allowed |= _numbers(json.dumps(quote))
    for chunk in chunks or []:
        allowed |= _numbers(_chunk_text(chunk))

    if quote is not None:
        discounts = {Decimal(str(line["discount_percent"])).normalize() for line in quote.get("lines", [])}
        # A percentage phrased as a discount/offer (discount, off, offer, apply, give, reduce nearby, within the same
        # clause) MUST be a discount_percent of the quote, whatever the policy chunks say. Any other percentage must
        # be stated in a chunk ("50% in advance").
        chunk_percents = {_num(m) for c in chunks or [] for m in PERCENT_RE.findall(_chunk_text(c))}
        bad = set()
        for m in PERCENT_RE.finditer(text):
            value = _num(m.group(1))
            if value in discounts:
                continue
            before = CLAUSE_END.split(text[max(0, m.start() - 30):m.start()])[-1]
            after = CLAUSE_END.split(text[m.end():m.end() + 30])[0]
            if DISCOUNT_WORDS.search(before + " " + after) or value not in chunk_percents:
                bad.add(str(value))
        bad = sorted(bad)
        if bad:
            problems.append(f"percentage not in the quote: {bad}")

    extra = sorted({n for n in _numbers(text) if n not in allowed})
    if extra:
        problems.append(f"uses numbers not in the result, quote or retrieved chunks: {[str(n) for n in extra]}")
    return problems
