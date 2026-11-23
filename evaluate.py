"""Run a folder of emails through the workflow and score them against an answer key.

Works on any folder of .txt emails. Emails without an answer-key entry are run and
shown, but not scored.

  python evaluate.py                                   # labelled emails, scored
  python evaluate.py --emails emails/practice
  python evaluate.py --emails path/to/new_emails --answers none
  python evaluate.py --url http://127.0.0.1:8000       # use a running API
"""
import argparse
from contextvars import ContextVar
import json
import logging
import math
import statistics
import time
from pathlib import Path
from typing import Callable

DATA = Path(__file__).resolve().parent
FIELDS = ["status", "items", "customer", "delivery_city", "deadline"]
EXTRACTION_FIELDS = {"items", "customer", "delivery_city", "deadline"}
ERROR_CATEGORIES = ("extraction_related", "business_rule_status", "workflow")
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")
_last_runner_metrics: ContextVar[dict | None] = ContextVar("evaluation_order_metrics", default=None)


class AnswerKeyError(ValueError):
    """The answer key does not have the structure required by the scorer."""


def norm_text(v):
    return v.strip().lower() if isinstance(v, str) else v


def validate_answer_key(data: object) -> dict:
    if not isinstance(data, dict):
        raise AnswerKeyError("Answer key must be a JSON object mapping email names to answers")
    for email, answer in data.items():
        if not isinstance(email, str) or not email:
            raise AnswerKeyError("Answer key email names must be non-empty strings")
        if not isinstance(answer, dict):
            raise AnswerKeyError(f"Answer for {email!r} must be a JSON object")
        missing = [field for field in FIELDS if field not in answer]
        if missing:
            raise AnswerKeyError(f"Answer for {email!r} is missing required fields: {', '.join(missing)}")
        if not isinstance(answer["status"], str):
            raise AnswerKeyError(f"Answer for {email!r} field 'status' must be a string")
        for field in ("customer", "delivery_city", "deadline"):
            if answer[field] is not None and not isinstance(answer[field], str):
                raise AnswerKeyError(f"Answer for {email!r} field {field!r} must be a string or null")
        if not isinstance(answer["items"], list):
            raise AnswerKeyError(f"Answer for {email!r} must have an items list")
        for index, item in enumerate(answer["items"]):
            if not isinstance(item, dict):
                raise AnswerKeyError(f"Answer for {email!r} item {index} must be a JSON object")
            missing_item_fields = [field for field in ("product_code", "requested") if field not in item]
            if missing_item_fields:
                raise AnswerKeyError(
                    f"Answer for {email!r} item {index} is missing required fields: "
                    f"{', '.join(missing_item_fields)}"
                )
            if item["product_code"] is not None and not isinstance(item["product_code"], str):
                raise AnswerKeyError(f"Answer for {email!r} item {index} product_code must be a string or null")
            requested = item["requested"]
            if requested is not None and (not isinstance(requested, int) or isinstance(requested, bool)):
                raise AnswerKeyError(f"Answer for {email!r} item {index} requested must be an integer or null")
    return data


def load_answers(path: str) -> dict:
    if path.lower() == "none":
        return {}
    answer_path = Path(path)
    if not answer_path.exists():
        return {}
    try:
        data = json.loads(answer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnswerKeyError(f"Could not read answer key {answer_path}: {exc}") from exc
    return validate_answer_key(data)


def _item_pairs(value: object) -> set | None:
    if not isinstance(value, list):
        return None
    pairs = set()
    for item in value:
        if not isinstance(item, dict) or "product_code" not in item or "requested" not in item:
            return None
        try:
            pairs.add((item["product_code"], item["requested"]))
        except TypeError:
            return None
    return pairs


def score(got: dict, want: dict) -> dict[str, bool]:
    """Use the comparison rules documented in DATA_PACK_README.md."""
    validate_answer_key({"answer": want})
    actual_items = _item_pairs(got.get("items")) if isinstance(got, dict) else None
    expected_items = _item_pairs(want["items"])
    return {
        "status": isinstance(got, dict) and got.get("status") == want["status"],
        "items": actual_items is not None and actual_items == expected_items,
        "customer": isinstance(got, dict) and norm_text(got.get("customer")) == norm_text(want["customer"]),
        "delivery_city": isinstance(got, dict) and norm_text(got.get("delivery_city")) == norm_text(want["delivery_city"]),
        "deadline": isinstance(got, dict) and got.get("deadline") == want["deadline"],
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
    from app.observability import OrderMetrics
    from app.steps.stock import StockRepository
    from app.workflow import OrderWorkflow

    s = get_settings()
    wf = OrderWorkflow(
        LLMClient(s),
        StockRepository(s),
        s.lead_time_days,
        s.extraction_max_iterations,
        s.llm_input_cost_per_million_tokens,
        s.llm_output_cost_per_million_tokens,
    )

    def run(text: str):
        metrics = OrderMetrics(
            s.llm_input_cost_per_million_tokens,
            s.llm_output_cost_per_million_tokens,
        )
        _last_runner_metrics.set(None)
        try:
            output = wf.run(text, metrics).model_dump()
        finally:
            _last_runner_metrics.set(metrics.to_dict())
        order_metrics = _last_runner_metrics.get()
        usage = order_metrics["llm"]["tokens"]
        return output, {"order_metrics": order_metrics, **usage}

    return run


def _usage_stats(usages: list[dict]) -> dict | None:
    if not usages:
        return None
    out = {}
    for field in TOKEN_FIELDS:
        values = [usage[field] for usage in usages if field in usage]
        if values:
            out[field] = {
                "count": len(values),
                "total": sum(values),
                "mean": round(statistics.mean(values), 3),
            }
    return out or None


def _normalize_usage(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for field in TOKEN_FIELDS:
        amount = value.get(field)
        if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
            normalized[field] = amount
    return normalized


def _latency_stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(p95, 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def _order_metrics(value: object) -> dict | None:
    return value if isinstance(value, dict) and isinstance(value.get("llm"), dict) else None


def _aggregate_observability(
    records: list[dict],
    fallback_latencies: list[float],
    expected_orders: int,
) -> dict:
    llm = {
        "calls": 0,
        "successful_calls": 0,
        "attempts": 0,
        "retries": 0,
        "tokens": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls_with_usage": 0,
            "calls_missing_usage": 0,
            "attempts_missing_usage": 0,
        },
    }
    errors = {}
    steps = {}
    llm_steps = {}
    order_latencies = []
    costs = []
    extraction_iterations = []
    fallback_count = 0
    reply_check_failures = 0

    for record in records:
        for field in ("calls", "successful_calls", "attempts", "retries"):
            llm[field] += record["llm"].get(field, 0)
        tokens = record["llm"].get("tokens", {})
        for field in llm["tokens"]:
            llm["tokens"][field] += tokens.get(field, 0)
        if record.get("total_duration_ms") is not None:
            order_latencies.append(record["total_duration_ms"])
        for category, count in record.get("error_counts", {}).items():
            errors[category] = errors.get(category, 0) + count
        for name, step in record.get("steps", {}).items():
            aggregate = steps.setdefault(name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
            aggregate["count"] += step.get("count", 0)
            aggregate["total_ms"] += step.get("total_ms", 0.0)
            aggregate["max_ms"] = max(aggregate["max_ms"], step.get("max_ms", 0.0))
        for name, step in record.get("llm_steps", {}).items():
            aggregate = llm_steps.setdefault(name, {})
            for field, value in step.items():
                aggregate[field] = aggregate.get(field, 0) + value
        extraction_iterations.append(record.get("extraction_iterations", 0))
        fallback_count += bool(record.get("fallback_reply_used"))
        reply_check_failures += record.get("reply_check_failures", 0)
        cost = record.get("estimated_cost", {})
        if cost.get("status") == "estimated" and isinstance(cost.get("value"), (int, float)):
            costs.append(cost["value"])

    if not records or len(records) != expected_orders:
        order_latencies = fallback_latencies
    for step in steps.values():
        step["mean_ms"] = round(step["total_ms"] / step["count"], 3) if step["count"] else None
        step["total_ms"] = round(step["total_ms"], 3)
        step["max_ms"] = round(step["max_ms"], 3)

    cost_status = (
        "estimated" if expected_orders > 0 and len(costs) == expected_orders
        else "partial" if costs
        else "not_available"
    )
    return {
        "orders_with_metrics": len(records),
        "latency_ms": _latency_stats(order_latencies),
        "llm": llm,
        "llm_steps": dict(sorted(llm_steps.items())),
        "step_latency_ms": dict(sorted(steps.items())),
        "extraction_iterations": {
            "total": sum(extraction_iterations),
            "mean": round(statistics.mean(extraction_iterations), 3) if extraction_iterations else None,
            "max": max(extraction_iterations) if extraction_iterations else None,
        },
        "fallback_reply_orders": fallback_count,
        "reply_check_failures": reply_check_failures,
        "error_counts": dict(sorted(errors.items())),
        "estimated_cost": {
            "value": round(sum(costs), 10) if costs else None,
            "status": cost_status,
            "orders_with_estimate": len(costs),
            "orders_without_estimate": max(0, expected_orders - len(costs)),
            "unit": "same currency/unit as configured rates",
            "basis": "sum of available per-order estimates; orders with unavailable costs are excluded",
        },
    }


def evaluate_files(
    files: list[Path],
    answers: dict,
    run: Callable | None,
    out_dir: Path,
    group_errors: bool = False,
) -> dict:
    """Run and score emails; runner results may optionally be ``(output, token_usage)``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if files and run is None:
        raise ValueError("A runner is required when the email folder is not empty")

    field_correct = dict.fromkeys(FIELDS, 0)
    exact_matches = 0
    scored_count = 0
    latencies: list[float] = []
    usages: list[dict] = []
    observability_records = []
    per_email = []
    available_keys = {f.stem for f in files}

    for path in sorted(files):
        key = path.stem
        is_scored = key in answers
        start = time.perf_counter()
        usage = {}
        order_metrics = None
        _last_runner_metrics.set(None)
        try:
            raw_result = run(path.read_text(encoding="utf-8"))
            latency_ms = (time.perf_counter() - start) * 1000
            if isinstance(raw_result, tuple) and len(raw_result) == 2:
                got, usage_value = raw_result
                usage = _normalize_usage(usage_value)
                order_metrics = _order_metrics(usage_value.get("order_metrics")) if isinstance(usage_value, dict) else None
            else:
                got = raw_result
            if not isinstance(got, dict):
                raise TypeError("workflow result must be a JSON object")
            (out_dir / f"{key}.json").write_text(
                json.dumps(got, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            latencies.append(latency_ms)
            if usage:
                usages.append(usage)
            if order_metrics:
                observability_records.append(order_metrics)
            field_scores = score(got, answers[key]) if is_scored else None
            if is_scored:
                scored_count += 1
                for field, correct in field_scores.items():
                    field_correct[field] += correct
                exact_match = all(field_scores.values())
                exact_matches += exact_match
                failures = [
                    {
                        "category": "business_rule_status" if field == "status" else "extraction_related",
                        "reason": f"{field}_mismatch",
                    }
                    for field in FIELDS
                    if not field_scores[field]
                ]
                detail = "" if exact_match else "  wrong: " + ", ".join(
                    field for field in FIELDS if not field_scores[field]
                )
                print(f"{key:10} {'PASS' if exact_match else 'FAIL'}  {got.get('status', 'unknown'):9}{detail}")
            else:
                exact_match = None
                failures = []
                print(
                    f"{key:10} -     {got.get('status', 'unknown'):9}  "
                    f"customer={got.get('customer')!r} city={got.get('delivery_city')!r} "
                    f"deadline={got.get('deadline')} issues={got.get('issues', [])}"
                )
            per_email.append(
                {
                    "email": key,
                    "scored": is_scored,
                    "exact_match": exact_match,
                    "field_scores": field_scores,
                    "failure_reasons": failures,
                    "latency_ms": round(latency_ms, 3),
                    "token_usage": usage or None,
                    "observability": order_metrics,
                }
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            latencies.append(latency_ms)
            order_metrics = _order_metrics(_last_runner_metrics.get())
            usage = _normalize_usage(getattr(run, "last_usage", None))
            if order_metrics:
                tokens = order_metrics["llm"].get("tokens", {})
                usage = _normalize_usage(tokens)
                observability_records.append(order_metrics)
            if usage:
                usages.append(usage)
            failure = {"category": "workflow", "reason": "execution_error"}
            field_scores = dict.fromkeys(FIELDS, False) if is_scored else None
            if is_scored:
                scored_count += 1
            print(f"{key:10} ERROR  {type(exc).__name__}: {exc}")
            per_email.append(
                {
                    "email": key,
                    "scored": is_scored,
                    "exact_match": False if is_scored else None,
                    "field_scores": field_scores,
                    "failure_reasons": [failure],
                    "latency_ms": round(latency_ms, 3),
                    "token_usage": usage or None,
                    "observability": order_metrics,
                    "error_type": type(exc).__name__,
                }
            )

    field_accuracy = {
        field: {
            "correct": field_correct[field],
            "total": scored_count,
            "accuracy": round(field_correct[field] / scored_count, 6) if scored_count else None,
        }
        for field in FIELDS
    }
    summary = {
        "schema_version": 1,
        "email_counts": {
            "processed": len(files),
            "scored": scored_count,
            "unscored": sum(path.stem not in answers for path in files),
            "failed": sum(bool(item.get("error_type")) for item in per_email),
            "answer_keys_without_email": sorted(set(answers) - available_keys),
        },
        "exact_order_match": {
            "correct": exact_matches,
            "total": scored_count,
            "accuracy": round(exact_matches / scored_count, 6) if scored_count else None,
            "item_order_independent": True,
        },
        "field_accuracy": field_accuracy,
        "failure_counts": {category: 0 for category in ERROR_CATEGORIES},
        "failure_category_notes": {
            "extraction_related": (
                "Customer, city, deadline, and item output mismatches; item mismatches may also "
                "reflect catalogue matching."
            ),
            "business_rule_status": (
                "Status mismatches; output alone cannot identify the particular validation or stock rule."
            ),
            "workflow": "Input or workflow execution exceptions.",
        },
        "latency_ms": _latency_stats(latencies),
        "token_usage": _usage_stats(usages),
        "observability": _aggregate_observability(observability_records, latencies, len(files)),
        "per_email": per_email,
    }
    for item in per_email:
        for failure in item["failure_reasons"]:
            summary["failure_counts"][failure["category"]] += 1

    if group_errors:
        grouped = {
            category: {"count": 0, "reasons": {}}
            for category in ERROR_CATEGORIES
        }
        for item in per_email:
            for failure in item["failure_reasons"]:
                category = grouped[failure["category"]]
                category["count"] += 1
                reason = failure["reason"]
                category["reasons"][reason] = category["reasons"].get(reason, 0) + 1
        for category in grouped.values():
            category["reasons"] = dict(sorted(category["reasons"].items()))
        summary["errors_by_category"] = grouped

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    if scored_count:
        print(
            f"\nTotal: {exact_matches}/{scored_count} fully correct, "
            f"field accuracy {sum(field_correct.values())}/{scored_count * len(FIELDS)}"
        )
    print(f"Evaluation summary in {out_dir / 'summary.json'}")
    print(f"Full results (with replies) in {out_dir}/")
    return summary


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", default=str(DATA / "emails" / "labelled"))
    ap.add_argument("--answers", default=str(DATA / "answer_key.json"), help="'none' to skip scoring")
    ap.add_argument("--url", default=None, help="Base URL of the running API (optional)")
    ap.add_argument("--out", default="results", help="Folder for the full JSON results")
    ap.add_argument("--verbose", action="store_true", help="Show workflow logs")
    ap.add_argument("--group-errors", action="store_true", help="Include error counts grouped by category")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    try:
        answers = load_answers(args.answers)
    except AnswerKeyError as exc:
        ap.error(str(exc))

    files = sorted(Path(args.emails).glob("*.txt"))
    out_dir = Path(args.out) / Path(args.emails).name
    run = make_runner(args.url) if files else None
    evaluate_files(files, answers, run, out_dir, args.group_errors)


if __name__ == "__main__":
    main()
