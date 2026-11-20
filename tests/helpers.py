from app.models import ExtractedOrder, ItemVerdict, Verdict, Verification


def ok() -> Verdict:
    return Verdict(correct=True, evidence=None, problem=None)


def all_correct(order: ExtractedOrder) -> Verification:
    return Verification(
        customer=ok(), delivery_city=ok(), deadline=ok(), refers_to_previous_order=ok(),
        items=[ItemVerdict(product_name=i.product_name, correct=True, evidence=None, problem=None)
               for i in order.items],
        missing_items=[],
    )
