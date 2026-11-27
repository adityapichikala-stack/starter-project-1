from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import app.llm_client as llm_module
from app.llm_client import LLMClient
from app.observability import OrderMetrics, estimate_cost, metrics_scope


def client(max_attempts=1):
    instance = LLMClient.__new__(LLMClient)
    instance._max_attempts = max_attempts
    instance._usage_callback = None
    return instance


def response(usage=None):
    return SimpleNamespace(
        model="mock-deployment",
        usage=usage,
    )


def test_missing_usage_is_counted_and_cost_is_unavailable():
    metrics = OrderMetrics(2.0, 4.0)

    with metrics_scope(metrics):
        client()._call("extract", lambda: response())

    data = metrics.to_dict()
    assert data["llm"]["calls"] == 1
    assert data["llm"]["successful_calls"] == 1
    assert data["llm"]["tokens"]["calls_missing_usage"] == 1
    assert data["llm"]["tokens"]["attempts_missing_usage"] == 0
    assert data["estimated_cost"]["value"] is None
    assert data["estimated_cost"]["status"] == "missing_usage"


def test_zero_token_usage_is_valid_and_costs_zero():
    metrics = OrderMetrics(2.0, 4.0)
    usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0)

    with metrics_scope(metrics):
        client()._call("verify", lambda: response(usage))

    data = metrics.to_dict()
    assert data["llm"]["tokens"]["calls_with_usage"] == 1
    assert data["llm"]["tokens"]["total_tokens"] == 0
    assert data["estimated_cost"]["value"] == 0.0
    assert estimate_cost(0, 0, 2.0, 4.0) == 0.0


def test_multiple_llm_calls_accumulate_usage_by_call():
    metrics = OrderMetrics()
    usages = [
        SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        SimpleNamespace(prompt_tokens=20, completion_tokens=3, total_tokens=23),
        SimpleNamespace(prompt_tokens=30, completion_tokens=5, total_tokens=35),
    ]

    with metrics_scope(metrics):
        for step, usage in zip(("extract", "verify", "reply"), usages):
            client()._call(step, lambda usage=usage: response(usage))

    data = metrics.to_dict()
    assert data["llm"]["calls"] == 3
    assert data["llm"]["successful_calls"] == 3
    assert data["llm"]["attempts"] == 3
    assert data["llm"]["retries"] == 0
    assert data["llm"]["tokens"] == {
        "prompt_tokens": 60,
        "completion_tokens": 10,
        "total_tokens": 70,
        "calls_with_usage": 3,
        "calls_missing_usage": 0,
        "attempts_missing_usage": 0,
    }
    assert data["llm_steps"]["extract"]["prompt_tokens"] == 10
    assert data["llm_steps"]["verify"]["completion_tokens"] == 3
    assert data["llm_steps"]["reply"]["total_tokens"] == 35


def test_retry_attempt_does_not_duplicate_logical_calls_or_tokens(monkeypatch):
    class TemporaryFailure(Exception):
        pass

    monkeypatch.setattr(llm_module, "RETRYABLE", (TemporaryFailure,))
    monkeypatch.setattr(llm_module.time, "sleep", lambda _: None)
    attempts = iter((TemporaryFailure(), response(
        SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    )))
    metrics = OrderMetrics()

    def request():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    with metrics_scope(metrics):
        result = client(max_attempts=2)._call("extract", request)

    data = metrics.to_dict()
    assert result.usage.prompt_tokens == 7
    assert data["llm"]["calls"] == 1
    assert data["llm"]["successful_calls"] == 1
    assert data["llm"]["attempts"] == 2
    assert data["llm"]["retries"] == 1
    assert data["llm"]["tokens"]["prompt_tokens"] == 7
    assert data["llm"]["tokens"]["completion_tokens"] == 3
    assert data["llm"]["tokens"]["attempts_missing_usage"] == 1


def test_concurrent_order_scopes_keep_token_metrics_separate():
    def run_order(prompt_tokens):
        metrics = OrderMetrics()
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=1,
            total_tokens=prompt_tokens + 1,
        )
        with metrics_scope(metrics):
            client()._call("extract", lambda: response(usage))
        return metrics.to_dict()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(run_order, (11, 29)))

    assert first["llm"]["calls"] == second["llm"]["calls"] == 1
    assert first["llm"]["tokens"]["prompt_tokens"] == 11
    assert second["llm"]["tokens"]["prompt_tokens"] == 29
    assert first["request_id"] != second["request_id"]


def test_missing_price_rates_do_not_claim_an_estimated_cost():
    metrics = OrderMetrics()
    metrics.llm_calls = 1
    metrics.prompt_tokens = 20
    metrics.completion_tokens = 10
    metrics.calls_with_usage = 1
    metrics.successful_llm_calls = 1

    assert metrics.to_dict()["estimated_cost"] == {
        "value": None,
        "input_cost_per_million_tokens": None,
        "output_cost_per_million_tokens": None,
        "status": "not_configured",
        "unit": "same currency/unit as configured rates",
        "basis": "provider-reported token counts; retry attempts without usage are not included",
    }


def test_embedding_usage_without_completion_tokens_is_counted():
    metrics = OrderMetrics()
    call_id = metrics.start_llm_call()
    metrics.record_llm_attempt(call_id, "embedding", 1, "success", 5.0,
                               usage={"prompt_tokens": 12, "total_tokens": 12})
    summary = metrics.to_dict()["llm"]["tokens"]
    assert summary["prompt_tokens"] == 12 and summary["completion_tokens"] == 0 and summary["total_tokens"] == 12
    assert summary["calls_with_usage"] == 1 and summary["calls_missing_usage"] == 0
    step = metrics.to_dict()["llm_steps"]["embedding"]
    assert step["prompt_tokens"] == 12 and step["calls_with_usage"] == 1 and step["calls_missing_usage"] == 0
