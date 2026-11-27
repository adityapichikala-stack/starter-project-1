"""The SP2 data pack is committed and internally consistent (no DB, no LLM)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "starter-project-2-data"


def test_data_pack_files_exist():
    assert (ROOT / "answer_key.json").is_file()
    assert (ROOT / "sp2_seed.sql").is_file()
    assert (ROOT / "docs" / "pricing_policy.md").is_file()
    assert list((ROOT / "docs" / "brochures").glob("*.md"))


def test_every_labelled_email_has_an_answer():
    key = json.loads((ROOT / "answer_key.json").read_text(encoding="utf-8"))
    emails = {p.stem for p in (ROOT / "emails" / "labelled").glob("email_*.txt")}
    assert emails and emails == set(key)
    for name, answer in key.items():
        assert answer["status"] in {"Ready", "Partial", "Need info"}, name
        assert (answer["status"] == "Need info") == (answer["quote"] is None), name
