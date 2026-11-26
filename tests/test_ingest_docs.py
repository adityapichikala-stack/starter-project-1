from scripts.ingest_docs import build_chunks, check_dimensions, chunk_markdown, prefix_for
import pytest

BROCHURE = """# Astra X9

Product code: PHN-X9

## Overview

6.7-inch 5G, 128 GB, black.

## Warranty

12 months.
"""


def write(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_chunk_markdown_splits_on_h2(tmp_path):
    p = write(tmp_path, "brochures/PHN-X9.md", BROCHURE)
    assert [h for h, _ in chunk_markdown(p)] == ["Introduction", "Overview", "Warranty"]


def test_brochure_prefix_uses_products_table_then_title(tmp_path):
    p = write(tmp_path, "brochures/PHN-X9.md", BROCHURE)
    assert prefix_for(p, "brochures/PHN-X9.md", "Overview", {"PHN-X9": "Astra X9"}) == "Product: Astra X9 (PHN-X9) — Overview"
    assert prefix_for(p, "brochures/PHN-X9.md", "Overview", {}) == "Product: Astra X9 (PHN-X9) — Overview"


def test_build_chunks_prefixes_text_and_policy(tmp_path):
    write(tmp_path, "brochures/PHN-X9.md", BROCHURE)
    write(tmp_path, "pricing_policy.md", "## Discounts\n\n5% over 10.\n")
    chunks = build_chunks(tmp_path, {"PHN-X9": "Astra X9"})
    texts = {(s, h): t for s, h, t in chunks}
    assert texts[("brochures/PHN-X9.md", "Warranty")] == "Product: Astra X9 (PHN-X9) — Warranty\n\n12 months."
    assert texts[("pricing_policy.md", "Discounts")].startswith("Pricing policy — Discounts\n\n")


def test_wrong_dimensions_stop_with_clear_error():
    with pytest.raises(SystemExit, match="expected 1536"):
        check_dimensions([[0.0] * 3], ["x / y"])
