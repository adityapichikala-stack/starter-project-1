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


# ---- RAG matching (fake retrieval + fake LLM) ----
from app.models import ProductCodeMatches
from app.rag import RetrievedChunk
from app.steps.rag_resolution import matching_codes

VALID = {"PHN-X9", "PHN-A5", "EAR-01"}


def _chunks():
    return [
        RetrievedChunk("brochures/PHN-X9.md", "Specs", "PHN-X9 black 128 GB 5G", 0.9),
        RetrievedChunk("brochures/PHN-A5.md", "Specs", "PHN-A5 blue 64 GB", 0.8),
        RetrievedChunk("pricing_policy.md", "Tiers", "mentions EAR-01", 0.7),
    ]


class _LLM:
    def __init__(self, codes):
        self.codes = codes

    def parse(self, step, messages, schema, **_):
        return ProductCodeMatches(matching_codes=self.codes)


def test_rag_fits_one():
    assert matching_codes(_LLM(["PHN-X9"]), "black 128 GB 5G phone", _chunks(), VALID) == ["PHN-X9"]


def test_rag_fits_two_is_ambiguous():
    codes = matching_codes(_LLM(["PHN-X9", "PHN-A5"]), "phone", _chunks(), VALID)
    code, issue = pure_description_match("phone", codes, {
        "a": CatalogueProduct("PHN-X9", "Astra X9", "1", 1), "b": CatalogueProduct("PHN-A5", "Astra A5", "1", 1)})
    assert code is None and issue == "Ambiguous product: phone (Astra A5 or Astra X9)"


def test_rag_fits_none_is_unknown():
    codes = matching_codes(_LLM([]), "wireless earbuds", _chunks(), VALID)
    assert codes == []
    assert pure_description_match("wireless earbuds", codes, catalogue())[1] == "Unknown product: wireless earbuds"


def test_rag_invented_or_non_brochure_code_ignored():
    # ZZZ-99 invented; EAR-01 only appears in a non-brochure chunk, so it is not a candidate
    assert matching_codes(_LLM(["ZZZ-99", "EAR-01", "PHN-X9"]), "x", _chunks(), VALID) == ["PHN-X9"]
