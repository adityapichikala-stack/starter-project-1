from app.steps.resolve_products import (
    CatalogueProduct,
    exact_catalogue_match,
    history_match,
    pure_description_match,
)


def catalogue():
    return {
        "phn-x9": CatalogueProduct("PHN-X9", "Astra X9", "34999", 6),
        "ear-01": CatalogueProduct("EAR-01", "Pebble Buds", "1299", 350),
        "ear-02": CatalogueProduct("EAR-02", "Pebble Buds Pro", "3499", 80),
    }


def test_exact_catalogue_match_is_case_and_space_insensitive():
    assert exact_catalogue_match("  ASTRA   X9 ", catalogue()).product_code == "PHN-X9"


def test_description_with_one_valid_candidate_resolves():
    code, issue = pure_description_match(
        "black 128 GB 5G phone",
        ["PHN-X9"],
        catalogue(),
    )
    assert code == "PHN-X9"
    assert issue is None


def test_description_with_multiple_candidates_is_ambiguous():
    code, issue = pure_description_match(
        "wireless earbuds",
        ["EAR-01", "EAR-02"],
        catalogue(),
    )
    assert code is None
    assert "Ambiguous product" in issue


def test_description_with_no_valid_candidate_is_unknown():
    code, issue = pure_description_match("missing product", [], catalogue())
    assert code is None
    assert issue == "Unknown product: missing product"


def test_history_returns_latest_order_for_exact_customer_name():
    rows = [
        ("Sahyadri Tech Solutions", "2026-10-02", "LAP-14", 5),
        ("Sahyadri Tech Solutions", "2026-10-02", "CHG-65", 5),
        ("Sahyadri Tech Solutions", "2026-08-14", "LAP-14", 3),
    ]
    items, issue = history_match("  sahyadri tech solutions ", rows)
    assert issue is None
    assert [(item.product_code, item.requested) for item in items] == [
        ("LAP-14", 5),
        ("CHG-65", 5),
    ]


def test_history_without_customer_match_returns_need_info():
    items, issue = history_match("Kaveri Hospitals", [])
    assert items == []
    assert "no order history" in issue
