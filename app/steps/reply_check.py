"""Code check on the LLM-written reply: it may only state what the computed result contains."""
import json
import re

FORBIDDEN = [
    (r"₹|\brs\.?\s*\d|\binr\b", "mentions money"),
    (r"\bprices?\b|\bpricing\b|\bcosts?\b|\binvoice\b", "mentions price/cost"),
    (r"\bdiscount", "mentions a discount"),
    (r"\d+\s*%|\bper\s*cent\b|\bpercent\b", "mentions a percentage"),
]


def _allowed_numbers(result: dict) -> set[int]:
    nums = {int(n) for n in re.findall(r"\d+", json.dumps(result))}
    return nums


def check_reply(reply: str, result: dict) -> list[str]:
    """Returns problems found; empty means the reply is OK to send."""
    problems = [why for pattern, why in FORBIDDEN if re.search(pattern, reply, re.I)]
    allowed = _allowed_numbers(result)
    extra = sorted({int(n) for n in re.findall(r"\d+", reply)} - allowed)
    if extra:
        problems.append(f"uses numbers not in the result: {extra}")
    return problems
