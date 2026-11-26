"""Step 4 (LLM): draft a reply from the computed result only. It never sees the raw email."""
import json

from app.llm_client import LLMClient

SYSTEM_PROMPT = """You write short, polite replies from the order desk of an electronics distributor \
to a customer who emailed a bulk order.

You are given the computed order result, a code-built quote (or null), and retrieved policy/brochure \
chunks as JSON. The quote is the only source of truth for prices, discounts, delivery, totals and \
validity. Retrieved chunks provide supporting product and policy information. Rules:
- Never calculate, invent or change prices, discounts, delivery charges, totals or quote validity.
- If quote is null, do not offer a quote or mention a price, discount or total.
- When quote is present, repeat only values from quote and say clearly that the quote is in INR.
- Say only what the result supports. Do not mention prices, totals, discounts, payment terms, delivery \
dates or anything else that is not in the result. Do not promise anything the result does not contain.
- Each item has "stock_note": "in_stock" (we can supply the requested quantity), "short" (we only have \
"available" units, fewer than requested), "unknown_product" (not in our catalogue) or "quantity_unclear".
- status "Ready": confirm we can supply every item in the quantities requested, list them, and say the \
team will arrange delivery to the city by the deadline if one is given.
- status "Partial": list the in_stock items we can supply in full. For each short item say clearly \
"you asked for X, we currently have Y". Ask whether they want the available quantity now, or prefer \
to wait or change the order.
- status "Need info": explain plainly what is missing or wrong (from the issues) and ask for exactly \
that. Do not confirm the order. Meaning of common issues: "Missing customer" = ask for the name of the \
organisation or company placing the order; "Missing delivery city" = ask which city to deliver to; \
"Unknown product: X" = say X is not in our catalogue and ask them to check the product name; \
"Invalid quantity" / "Missing quantity" = ask for the exact number of units; a deadline issue = say the \
date is too soon for us to deliver and ask whether a later date works; "Refers to a previous order" / \
"No items listed" = ask them to list the products and quantities they need.
- If the result also has stock shortages, mention them briefly too.
- Use only numbers that appear in the result. Do not calculate anything (no differences, no totals). \
Write the deadline as a date like "30 November 2026".
- Address the contact person by name if given, otherwise the organisation. Plain text, under 150 words. \
Sign off as "Order Desk". No subject line."""


def fallback_reply(result: dict, contact_name: str | None) -> str:
    """Used if the LLM is unavailable, so the endpoint still returns the contract shape."""
    who = contact_name or result.get("customer") or "there"
    status = result["status"]
    if status == "Ready":
        body = "Thank you for your order. We can supply all items in the quantities requested."
    elif status == "Partial":
        body = "Thank you for your order. Some items are short on stock:\n- " + "\n- ".join(result["issues"]) + \
               "\nPlease let us know whether you would like the available quantity now."
    else:
        body = "Thank you for your email. Before we can process this order we need the following:\n- " + \
               "\n- ".join(result["issues"])
    return f"Dear {who},\n\n{body}\n\nRegards,\nOrder Desk"


def _stock_note(item: dict) -> str:
    if item["product_code"] is None:
        return "unknown_product"
    if item["requested"] is None:
        return "quantity_unclear"
    return "short" if item["requested"] > item["available"] else "in_stock"


def draft_reply(llm: LLMClient, result: dict, contact_name: str | None, quote: dict | None = None,
                retrieved_chunks: list | None = None) -> str:
    items = [{**i, "stock_note": _stock_note(i)} for i in result["items"]]
    payload = {
        "contact_name": contact_name or None,
        **result,
        "items": items,
        "quote": quote,
        "retrieved_chunks": retrieved_chunks or [],
    }
    return llm.complete(
        "reply",
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
        ],
    )
