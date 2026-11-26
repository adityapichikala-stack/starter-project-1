"""Pydantic models passed between the workflow steps."""
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


# ---- Step 1 output (LLM extraction). No status, no prices: the model decides nothing. ----
class ExtractedItem(BaseModel):
    product_name: str = Field(
        description="Product name exactly as the customer wrote it, without the quantity "
        "or words like 'x', 'units', 'pcs'. Do not correct or normalise it."
    )
    quantity: int | None = Field(
        description="The quantity as a whole number, only if the email clearly states a number "
        "(number words like 'ten' become 10). null if vague ('a few', 'some') or not given."
    )
    quantity_text: str | None = Field(
        description="The quantity exactly as written in the email, e.g. '10' or 'a few'. null if none given."
    )


class ExtractedOrder(BaseModel):
    customer: str | None = Field(
        description="The ordering organisation's name (company, school, hospital, shop). "
        "null if only a person's name is given."
    )
    delivery_city: str | None = Field(
        description="The city to deliver to, just the city name. null if not stated."
    )
    deadline: str | None = Field(
        description="Latest delivery date as YYYY-MM-DD, relative dates resolved against the email date. "
        "null if no deadline is given."
    )
    deadline_text: str | None = Field(
        description="The deadline phrase copied exactly from the email, e.g. 'by 24 November 2026' or "
        "'a week from today'. null if no deadline."
    )
    refers_to_previous_order: bool = Field(
        description="true if the email asks to repeat or refers to a previous order instead of listing items."
    )
    items: list[ExtractedItem] = Field(description="Every product requested. Empty if none are listed.")


# ---- Step 1b output (LLM cross-check). Only verdicts and quotes: the verifier never supplies values. ----
class Verdict(BaseModel):
    correct: bool = Field(
        description="true if the extracted value is right per the email and the rules. For a null value, "
        "true means the email really does not give it."
    )
    evidence: str | None = Field(
        description="Shortest exact quote from the email that supports the value. null when the value is null."
    )
    problem: str | None = Field(
        description="If not correct: what is wrong and what the email actually says. null if correct."
    )


class ItemVerdict(Verdict):
    product_name: str = Field(description="The extracted product_name this verdict is about")


class Verification(BaseModel):
    customer: Verdict
    delivery_city: Verdict
    deadline: Verdict
    refers_to_previous_order: Verdict
    items: list[ItemVerdict] = Field(description="One verdict per extracted item, same order (name + quantity)")
    missing_items: list[str] = Field(
        description="Products the email orders that the extraction left out. Empty if none."
    )


# ---- Step 2 output (code validation) ----
class ValidatedItem(BaseModel):
    product_name: str
    requested: int | None


class ValidationResult(BaseModel):
    deadline: str | None
    items: list[ValidatedItem]
    issues: list[str]


# ---- Step 3 (stock) ----
class CatalogueEntry(BaseModel):
    product_code: str
    name: str
    quantity_available: int
    unit_price_inr: Decimal | None = None


class OrderItem(BaseModel):
    product_name: str
    product_code: str | None
    requested: int | None
    available: int | None


Status = Literal["Ready", "Partial", "Need info"]


class StockResult(BaseModel):
    items: list[OrderItem]
    unknown_issues: list[str]
    shortage_issues: list[str]


# ---- Starter Project 2: resolved products and code-built quote ----
class ResolvedItem(BaseModel):
    product_name: str
    product_code: str | None
    requested: int | None
    available: int | None
    unit_price_inr: Decimal | None = None
    resolution: Literal["exact", "description", "history", "unknown", "ambiguous"] = "unknown"


class ResolutionResult(BaseModel):
    items: list[ResolvedItem]
    issues: list[str]


class QuoteLine(BaseModel):
    product_code: str
    quantity: int
    unit_price_inr: Decimal
    discount_percent: int
    line_total_inr: Decimal

    @field_serializer("unit_price_inr", "line_total_inr")
    def serialize_money(self, value: Decimal) -> float:
        return float(value)


class Quote(BaseModel):
    lines: list[QuoteLine]
    subtotal_inr: Decimal
    delivery_inr: Decimal
    total_inr: Decimal
    valid_until: str

    @field_serializer("subtotal_inr", "delivery_inr", "total_inr")
    def serialize_money(self, value: Decimal) -> float:
        return float(value)


class ProductCodeChoice(BaseModel):
    product_code: str | None = Field(
        description="Exactly one valid product code from the supplied candidate list, or null if no candidate matches."
    )


# ---- API contract ----
class OrderRequest(BaseModel):
    email: str = Field(min_length=1, description="The raw email text, headers included")


class OrderResult(BaseModel):
    customer: str | None
    delivery_city: str | None
    deadline: str | None
    items: list[OrderItem]
    status: Status
    issues: list[str]
    reply: str
    quote: Quote | None = None
