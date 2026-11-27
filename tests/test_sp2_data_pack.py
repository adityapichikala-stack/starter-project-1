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


REPO = Path(__file__).resolve().parents[1]


def test_no_root_copies_of_the_sp2_data_pack():
    """starter-project-2-data/ is the single source; copies at the repo root must not come back."""
    assert not (REPO / "sp2_seed.sql").exists()
    assert not (REPO / "docs" / "brochures").exists()
    assert not (REPO / "docs" / "pricing_policy.md").exists()
    sp2_numbers = {f"email_{n}.txt" for n in range(21, 36)}
    for folder in ("labelled", "practice"):
        stray = sp2_numbers & {p.name for p in (REPO / "emails" / folder).glob("email_*.txt")}
        assert not stray, f"SP2 emails copied into emails/{folder}: {sorted(stray)}"


def test_compose_mounts_the_data_pack_seed():
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./starter-project-2-data/sp2_seed.sql:/docker-entrypoint-initdb.d/02_sp2_seed.sql" in compose
    assert "./sp2_seed.sql" not in compose


def test_ingest_creates_the_documents_table_itself():
    ingest = (REPO / "scripts" / "ingest_docs.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS documents" in ingest and "vector(1536)" in ingest
    assert "documents" not in (ROOT / "sp2_seed.sql").read_text(encoding="utf-8")  # the pack seed does not define it
