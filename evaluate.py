"""Run a folder of emails through the workflow and score them against an answer key.

Works on any folder of .txt emails (e.g. new examples at demo time). Emails without an
entry in the answer key are run and shown, but not scored.

  python evaluate.py                                   # 10 labelled emails, scored
  python evaluate.py --emails emails/practice
  python evaluate.py --emails path/to/new_emails --answers none
  python evaluate.py --url http://127.0.0.1:8000       # go through the running API instead
"""
import argparse
import json
import logging
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent
FIELDS = ["status", "items", "customer", "delivery_city", "deadline"]


def norm_text(v):
    return v.strip().lower() if isinstance(v, str) else v


def score(got: dict, want: dict) -> dict[str, bool]:
    """Scoring rules from the data pack README."""
    return {
        "status": got["status"] == want["status"],
        "items": {(i["product_code"], i["requested"]) for i in got["items"]}
        == {(i["product_code"], i["requested"]) for i in want["items"]},
        "customer": norm_text(got["customer"]) == norm_text(want["customer"]),
        "delivery_city": norm_text(got["delivery_city"]) == norm_text(want["delivery_city"]),
        "deadline": got["deadline"] == want["deadline"],
    }


def make_runner(url: str | None):
    if url:
        import httpx

        def run(text: str) -> dict:
            r = httpx.post(f"{url.rstrip('/')}/orders", json={"email": text}, timeout=180)
            r.raise_for_status()
            return r.json()

        return run

    from app.config import get_settings
    from app.llm_client import LLMClient
    from app.steps.stock import StockRepository
    from app.workflow import OrderWorkflow

    s = get_settings()
    wf = OrderWorkflow(LLMClient(s), StockRepository(s), s.lead_time_days, s.extraction_max_iterations)
    return lambda text: wf.run(text).model_dump()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", default=str(DATA / "emails" / "labelled"))
    ap.add_argument("--answers", default=str(DATA / "answer_key.json"), help="'none' to skip scoring")
    ap.add_argument("--url", default=None, help="Base URL of the running API (optional)")
    ap.add_argument("--out", default="results", help="Folder for the full JSON results")
    ap.add_argument("--verbose", action="store_true", help="Show workflow logs")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    answers = {}
    if args.answers.lower() != "none" and Path(args.answers).exists():
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))

    files = sorted(Path(args.emails).glob("*.txt"))
    if not files:
        sys.exit(f"No .txt emails in {args.emails}")

    run = make_runner(args.url)
    out_dir = Path(args.out) / Path(args.emails).name
    out_dir.mkdir(parents=True, exist_ok=True)

    scored = passed = field_ok = field_total = 0
    for f in files:
        key = f.stem
        try:
            got = run(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{key:10} ERROR  {type(e).__name__}: {e}")
            if key in answers:
                scored += 1
                field_total += len(FIELDS)
            continue
        (out_dir / f"{key}.json").write_text(json.dumps(got, indent=2, ensure_ascii=False), encoding="utf-8")

        if key in answers:
            res = score(got, answers[key])
            scored += 1
            field_total += len(res)
            field_ok += sum(res.values())
            ok = all(res.values())
            passed += ok
            misses = [k for k, v in res.items() if not v]
            detail = "" if ok else "  wrong: " + ", ".join(
                f"{m} (got {got[m] if m != 'items' else sorted((i['product_code'], i['requested']) for i in got['items'])!r}, "
                f"want {answers[key][m] if m != 'items' else sorted((i['product_code'], i['requested']) for i in answers[key]['items'])!r})"
                for m in misses
            )
            print(f"{key:10} {'PASS' if ok else 'FAIL'}  {got['status']:9}{detail}")
        else:
            print(f"{key:10} -     {got['status']:9}  customer={got['customer']!r} city={got['delivery_city']!r} "
                  f"deadline={got['deadline']} issues={got['issues']}")

    if scored:
        print(f"\nTotal: {passed}/{scored} emails fully correct, {field_ok}/{field_total} fields correct")
    print(f"Full results (with replies) in {out_dir}/")


if __name__ == "__main__":
    main()
