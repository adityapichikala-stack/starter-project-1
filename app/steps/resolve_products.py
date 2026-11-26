"""SP2 product resolution. A model may only choose a candidate code supplied by code/RAG."""
import re
from dataclasses import dataclass
from decimal import Decimal

from app.models import ResolvedItem, ResolutionResult, ValidatedItem

REPEAT_RE = re.compile(r"\b(same as (?:our |the )?last (?:order|time)|repeat (?:our |the )?(?:previous|last) order)\b", re.I)


@dataclass(frozen=True)
class CatalogueProduct:
    product_code: str
    name: str
    unit_price_inr: Decimal
    quantity_available: int


def normalize_name(value: str) -> str:
    return " ".join(value.split()).lower()


def catalogue_product_by_code(
    code: str,
    products: dict[str, CatalogueProduct],
) -> CatalogueProduct | None:
    for product in products.values():
        if product.product_code == code:
            return product
    return products.get(code)


def exact_catalogue_match(text: str, products: dict[str, CatalogueProduct]) -> CatalogueProduct | None:
    normalized = normalize_name(text)
    if normalized in products:
        return products[normalized]
    for product in products.values():
        if normalize_name(product.name) == normalized:
            return product
    return None


def pure_description_match(
    description: str,
    candidate_codes: list[str],
    products: dict[str, CatalogueProduct],
) -> tuple[str | None, str | None]:
    """Returns (code, issue). Exactly one valid candidate code is required."""
    valid = {
        code
        for code in candidate_codes
        if code in products
        or any(product.product_code == code for product in products.values())
    }
    if len(valid) == 1:
        code = next(iter(valid))
        return code, None
    if len(valid) > 1:
        names = sorted(
            next(product.name for product in products.values() if product.product_code == code)
            if code not in products
            else products[code].name
            for code in valid
        )
        return None, f"Ambiguous product: {description} ({' or '.join(names)})"
    return None, f"Unknown product: {description}"


def history_match(
    customer: str | None,
    history_rows: list[tuple[str, str, str, int]],
) -> tuple[list[ResolvedItem], str | None]:
    """history_rows: (customer, order_date, product_code, quantity), newest first."""
    if not customer:
        return [], "Repeat our previous order, but there is no order history"
    wanted = normalize_name(customer)
    for row_customer, _, _, _ in history_rows:
        if normalize_name(row_customer) == wanted:
            break
    else:
        return [], f"Repeat our previous order, but there is no order history for {customer}"

    latest_date = max(row[1] for row in history_rows if normalize_name(row[0]) == wanted)
    items = [
        ResolvedItem(
            product_name=product_code,
            product_code=product_code,
            requested=quantity,
            available=None,
            resolution="history",
        )
        for row_customer, order_date, product_code, quantity in history_rows
        if normalize_name(row_customer) == wanted and order_date == latest_date
    ]
    return items, None


def resolve_validated_items(
    items: list[ValidatedItem],
    customer: str | None,
    repeat_requested: bool,
    catalogue: dict[str, CatalogueProduct],
    history_rows: list[tuple[str, str, str, int]],
    rag_candidates: dict[str, list[str]],
) -> ResolutionResult:
    if repeat_requested:
        history, issue = history_match(customer, history_rows)
        if issue:
            return ResolutionResult(items=[], issues=[issue])
        resolved_history = []
        for item in history:
            product = catalogue.get(item.product_code) if item.product_code else None
            resolved_history.append(
                ResolvedItem(
                    product_name=product.name if product else item.product_code,
                    product_code=item.product_code,
                    requested=item.requested,
                    available=product.quantity_available if product else None,
                    unit_price_inr=product.unit_price_inr if product else None,
                    resolution="history",
                )
            )
        return ResolutionResult(items=resolved_history, issues=[])

    resolved: list[ResolvedItem] = []
    issues: list[str] = []
    for item in items:
        name = normalize_name(item.product_name)
        exact = exact_catalogue_match(item.product_name, catalogue)
        if exact:
            resolved.append(
                ResolvedItem(
                    product_name=item.product_name,
                    product_code=exact.product_code,
                    requested=item.requested,
                    available=exact.quantity_available,
                    unit_price_inr=exact.unit_price_inr,
                    resolution="exact",
                )
            )
            continue

        code, issue = pure_description_match(
            item.product_name,
            rag_candidates.get(item.product_name, []),
            catalogue,
        )
        if issue:
            issues.append(issue)
            resolved.append(
                ResolvedItem(
                    product_name=item.product_name,
                    product_code=None,
                    requested=item.requested,
                    available=None,
                    resolution="ambiguous" if issue.startswith("Ambiguous") else "unknown",
                )
            )
            continue

        product = catalogue_product_by_code(code, catalogue)
        if product is None:
            issues.append(f"Unknown product: {item.product_name}")
            resolved.append(
                ResolvedItem(
                    product_name=item.product_name,
                    product_code=None,
                    requested=item.requested,
                    available=None,
                    resolution="unknown",
                )
            )
            continue
        resolved.append(
            ResolvedItem(
                product_name=item.product_name,
                product_code=code,
                requested=item.requested,
                available=product.quantity_available,
                unit_price_inr=product.unit_price_inr,
                resolution="description",
            )
        )
    return ResolutionResult(items=resolved, issues=issues)
