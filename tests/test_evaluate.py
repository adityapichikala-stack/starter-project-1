import json

import pytest

from evaluate import AnswerKeyError, evaluate_files, load_answers, score, validate_answer_key
from evaluate import _latency_stats
from evaluate import score_quote


def expected(**overrides):
    answer = {
        "status": "Ready",
        "items": [
            {"product_code": "A", "requested": 3},
            {"product_code": "B", "requested": 1},
        ],
        "customer": "Acme Ltd",
        "delivery_city": "Pune",
        "deadline": "2026-12-01",
    }
    answer.update(overrides)
    return answer


def result(**overrides):
    output = {
        "status": "Ready",
        "items": [
            {"product_code": "B", "requested": 1},
            {"product_code": "A", "requested": 3},
        ],
        "customer": "  ACME Ltd ",
        "delivery_city": "PUNE",
        "deadline": "2026-12-01",
    }
    output.update(overrides)
    return output


def test_score_preserves_existing_normalization_and_order_independent_item_comparison():
    assert score(result(), expected()) == {
        "status": True,
        "items": True,
        "customer": True,
        "delivery_city": True,
        "deadline": True,
    }


def test_score_reports_each_field_without_changing_other_field_results():
    scored = score(
        result(status="Partial", customer="Different"),
        expected(),
    )
    assert scored == {
        "status": False,
        "items": True,
        "customer": False,
        "delivery_city": True,
        "deadline": True,
    }


def test_evaluation_reports_field_accuracy_and_exact_match_separately(tmp_path):
    first = tmp_path / "email_01.txt"
    second = tmp_path / "email_02.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    outputs = {
        "first": (result(), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        "second": result(status="Need info", delivery_city="Mumbai"),
    }
    answers = {"email_01": expected(), "email_02": expected()}

    summary = evaluate_files(
        [second, first],
        answers,
        lambda text: outputs[text],
        tmp_path / "results",
        group_errors=True,
    )

    assert summary["exact_order_match"] == {
        "correct": 1,
        "total": 2,
        "accuracy": 0.5,
        "item_order_independent": True,
    }
    assert summary["field_accuracy"]["items"] == {"correct": 2, "total": 2, "accuracy": 1.0}
    assert summary["field_accuracy"]["status"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert summary["field_accuracy"]["delivery_city"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert summary["failure_counts"] == {
        "extraction_related": 1,
        "business_rule_status": 1,
        "workflow": 0,
    }
    assert summary["errors_by_category"]["extraction_related"]["reasons"] == {
        "delivery_city_mismatch": 1
    }
    assert summary["token_usage"] == {
        "prompt_tokens": {"count": 1, "total": 10, "mean": 10},
        "completion_tokens": {"count": 1, "total": 5, "mean": 5},
        "total_tokens": {"count": 1, "total": 15, "mean": 15},
    }
    assert json.loads((tmp_path / "results" / "summary.json").read_text(encoding="utf-8")) == summary


def test_workflow_failures_are_distinguished_from_scoring_mismatches(tmp_path):
    email = tmp_path / "email_01.txt"
    email.write_text("email", encoding="utf-8")

    def failing_runner(_):
        raise RuntimeError("workflow unavailable")

    summary = evaluate_files(
        [email],
        {"email_01": expected()},
        failing_runner,
        tmp_path / "results",
        group_errors=True,
    )

    assert summary["email_counts"]["failed"] == 1
    assert summary["exact_order_match"]["accuracy"] == 0.0
    assert all(metric["correct"] == 0 for metric in summary["field_accuracy"].values())
    assert summary["errors_by_category"]["workflow"] == {
        "count": 1,
        "reasons": {"execution_error": 1},
    }


def test_empty_input_produces_a_valid_zero_email_summary_without_a_runner(tmp_path):
    summary = evaluate_files([], {}, None, tmp_path / "results")

    assert summary["email_counts"]["processed"] == 0
    assert summary["exact_order_match"]["accuracy"] is None
    assert all(metric["accuracy"] is None for metric in summary["field_accuracy"].values())
    assert summary["latency_ms"]["count"] == 0
    assert summary["token_usage"] is None
    assert json.loads((tmp_path / "results" / "summary.json").read_text(encoding="utf-8")) == summary


def test_answer_key_must_be_an_object():
    with pytest.raises(AnswerKeyError, match="JSON object"):
        validate_answer_key([])


def test_answer_key_rejects_malformed_item_data():
    with pytest.raises(AnswerKeyError, match="items list"):
        validate_answer_key({"email_01": expected(items={"product_code": "A"})})


def test_missing_expected_fields_are_reported_clearly():
    incomplete = expected()
    del incomplete["deadline"]

    with pytest.raises(AnswerKeyError, match="missing required fields: deadline"):
        score(result(), incomplete)


def test_load_answers_reports_invalid_json(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(AnswerKeyError, match="Could not read answer key"):
        load_answers(str(path))


def test_latency_summary_includes_deterministic_p95():
    assert _latency_stats([10, 20, 30, 40, 50, 60, 70, 80, 90, 100]) == {
        "count": 10,
        "mean": 55,
        "median": 55.0,
        "p95": 100,
        "min": 10,
        "max": 100,
    }


def test_evaluation_aggregates_order_cost_tokens_retries_and_steps(tmp_path):
    emails = []
    answers = {}
    outputs = {}
    for index, latency, prompt, completion in (
        (1, 100.0, 100, 50),
        (2, 200.0, 200, 100),
    ):
        key = f"email_0{index}"
        path = tmp_path / f"{key}.txt"
        path.write_text(key, encoding="utf-8")
        emails.append(path)
        answers[key] = expected()
        order_metrics = {
            "request_id": f"request-{index}",
            "total_duration_ms": latency,
            "llm": {
                "calls": 2,
                "successful_calls": 2,
                "attempts": 3,
                "retries": 1,
                "tokens": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                    "calls_with_usage": 2,
                    "calls_missing_usage": 0,
                    "attempts_missing_usage": 1,
                },
            },
            "llm_steps": {
                "extract": {
                    "calls": 1,
                    "successful_calls": 1,
                    "attempts": 2,
                    "retries": 1,
                    "prompt_tokens": prompt,
                    "completion_tokens": 0,
                    "total_tokens": prompt,
                    "calls_with_usage": 1,
                    "calls_missing_usage": 0,
                },
                "verify": {
                    "calls": 1,
                    "successful_calls": 1,
                    "attempts": 1,
                    "retries": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": completion,
                    "total_tokens": completion,
                    "calls_with_usage": 1,
                    "calls_missing_usage": 0,
                },
            },
            "steps": {"extract": {"count": 1, "total_ms": latency / 2, "max_ms": latency / 2}},
            "extraction_iterations": 1,
            "fallback_reply_used": index == 2,
            "reply_check_failures": index - 1,
            "error_counts": {"rate_limit": 1},
            "estimated_cost": {
                "value": (prompt * 2 + completion * 4) / 1_000_000,
                "status": "estimated",
            },
        }
        outputs[key] = (
            result(),
            {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "order_metrics": order_metrics,
            },
        )

    summary = evaluate_files(
        emails,
        answers,
        lambda text: outputs[text],
        tmp_path / "report",
    )

    metrics = summary["observability"]
    assert metrics["latency_ms"]["mean"] == 150
    assert metrics["latency_ms"]["p95"] == 200
    assert metrics["llm"]["calls"] == 4
    assert metrics["llm"]["attempts"] == 6
    assert metrics["llm"]["retries"] == 2
    assert metrics["llm"]["tokens"]["prompt_tokens"] == 300
    assert metrics["llm"]["tokens"]["completion_tokens"] == 150
    assert metrics["llm_steps"]["extract"]["retries"] == 2
    assert metrics["llm_steps"]["extract"]["prompt_tokens"] == 300
    assert metrics["llm_steps"]["verify"]["completion_tokens"] == 150
    assert metrics["fallback_reply_orders"] == 1
    assert metrics["reply_check_failures"] == 1
    assert metrics["error_counts"] == {"rate_limit": 2}
    assert metrics["step_latency_ms"]["extract"]["count"] == 2
    assert metrics["estimated_cost"]["status"] == "estimated"
    assert metrics["estimated_cost"]["value"] == pytest.approx(0.0012)


def test_sp2_quote_null_matches_only_null():
    assert score_quote(None, None) is True
    assert score_quote(None, {"lines": []}) is False


def test_sp2_quote_score_compares_exact_quote_values():
    want = {
        "lines": [
            {"product_code": "EAR-01", "quantity": 60,
             "discount_percent": 8, "line_total_inr": 71704.8}
        ],
        "subtotal_inr": 71704.8,
        "delivery_inr": 0.0,
        "total_inr": 71704.8,
        "valid_until": "2026-11-27",
    }
    got = {
        "lines": [
            {"product_code": "EAR-01", "quantity": 60,
             "discount_percent": 8, "line_total_inr": 71704.8}
        ],
        "subtotal_inr": 71704.8,
        "delivery_inr": 0,
        "total_inr": 71704.8,
        "valid_until": "2026-11-27",
    }
    assert score_quote(got, want) is True


def test_fail_line_names_quote_and_other_failed_fields(tmp_path, capsys):
    want = {"status": "Ready", "items": [], "customer": "A", "delivery_city": "B", "deadline": None,
            "quote": {"lines": [], "subtotal_inr": 1, "delivery_inr": 0, "total_inr": 1, "valid_until": "2026-01-01"}}
    got = {**want, "customer": "a", "status": "Partial", "quote": None, "reply": "hi"}
    email = tmp_path / "email_1.txt"
    email.write_text("x", encoding="utf-8")
    evaluate_files([email], {"email_1": want}, lambda text: got, tmp_path / "out")
    out = capsys.readouterr().out
    assert "FAIL" in out and "wrong: status, quote" in out
    assert "0/1 fully correct" in out
