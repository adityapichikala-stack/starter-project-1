"""Request-scoped workflow metrics with no external monitoring dependencies."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator
from uuid import uuid4

log = logging.getLogger("observability")
_current_metrics: ContextVar["OrderMetrics | None"] = ContextVar("order_metrics", default=None)


def estimate_cost(
    prompt_tokens: int | None,
    completion_tokens: int | None,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> float | None:
    if (
        prompt_tokens is None
        or completion_tokens is None
        or input_cost_per_million is None
        or output_cost_per_million is None
    ):
        return None
    return (
        prompt_tokens * input_cost_per_million
        + completion_tokens * output_cost_per_million
    ) / 1_000_000


@dataclass
class OrderMetrics:
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)
    total_duration_ms: float | None = None
    llm_calls: int = 0
    successful_llm_calls: int = 0
    llm_attempts: int = 0
    llm_retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls_with_usage: int = 0
    calls_missing_usage: int = 0
    attempts_missing_usage: int = 0
    extraction_iterations: int = 0
    fallback_reply_used: bool = False
    reply_check_failures: int = 0
    error_counts: dict[str, int] = field(default_factory=dict)
    step_metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)
    llm_step_metrics: dict[str, dict[str, int]] = field(default_factory=dict)

    def start_llm_call(self) -> int:
        self.llm_calls += 1
        return self.llm_calls

    def record_llm_attempt(
        self,
        call_id: int,
        step: str,
        attempt: int,
        outcome: str,
        duration_ms: float,
        error_category: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.llm_attempts += 1
        self.add_step_duration(f"llm_{step}", duration_ms)
        step_metrics = self.llm_step_metrics.setdefault(
            step,
            {
                "calls": 0,
                "successful_calls": 0,
                "attempts": 0,
                "retries": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls_with_usage": 0,
                "calls_missing_usage": 0,
            },
        )
        if attempt == 1:
            step_metrics["calls"] += 1
        step_metrics["attempts"] += 1
        if attempt > 1:
            self.llm_retries += 1
            step_metrics["retries"] += 1
        if outcome == "success":
            self.successful_llm_calls += 1
            step_metrics["successful_calls"] += 1
            if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
                self.prompt_tokens += usage["prompt_tokens"]
                self.completion_tokens += usage["completion_tokens"]
                total_tokens = usage.get(
                    "total_tokens", usage["prompt_tokens"] + usage["completion_tokens"]
                )
                self.total_tokens += total_tokens
                self.calls_with_usage += 1
                step_metrics["prompt_tokens"] += usage["prompt_tokens"]
                step_metrics["completion_tokens"] += usage["completion_tokens"]
                step_metrics["total_tokens"] += total_tokens
                step_metrics["calls_with_usage"] += 1
            else:
                self.calls_missing_usage += 1
                step_metrics["calls_missing_usage"] += 1
        elif error_category:
            self.attempts_missing_usage += 1
            self.record_error(error_category)

        log.info(
            "event=llm_attempt request_id=%s step=%s call=%d attempt=%d outcome=%s "
            "duration_ms=%.3f error_category=%s prompt_tokens=%s completion_tokens=%s",
            self.request_id,
            step,
            call_id,
            attempt,
            outcome,
            duration_ms,
            error_category or "-",
            (usage or {}).get("prompt_tokens", "-"),
            (usage or {}).get("completion_tokens", "-"),
        )

    def record_error(self, category: str) -> None:
        self.error_counts[category] = self.error_counts.get(category, 0) + 1

    def add_step_duration(self, step: str, duration_ms: float) -> None:
        entry = self.step_metrics.setdefault(step, {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
        entry["count"] += 1
        entry["total_ms"] += duration_ms
        entry["max_ms"] = max(entry["max_ms"], duration_ms)

    def to_dict(self, total_duration_ms: float | None = None) -> dict:
        if total_duration_ms is None:
            total_duration_ms = self.total_duration_ms
        rates_configured = (
            self.input_cost_per_million is not None
            and self.output_cost_per_million is not None
        )
        usage_complete = self.calls_missing_usage == 0 and self.attempts_missing_usage == 0
        cost = estimate_cost(
            self.prompt_tokens if usage_complete and self.llm_calls else None,
            self.completion_tokens if usage_complete and self.llm_calls else None,
            self.input_cost_per_million,
            self.output_cost_per_million,
        )
        return {
            "request_id": self.request_id,
            "total_duration_ms": round(total_duration_ms, 3) if total_duration_ms is not None else None,
            "llm": {
                "calls": self.llm_calls,
                "successful_calls": self.successful_llm_calls,
                "attempts": self.llm_attempts,
                "retries": self.llm_retries,
                "tokens": {
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                    "total_tokens": self.total_tokens,
                    "calls_with_usage": self.calls_with_usage,
                    "calls_missing_usage": self.calls_missing_usage,
                    "attempts_missing_usage": self.attempts_missing_usage,
                },
            },
            "steps": {
                name: {
                    **values,
                    "total_ms": round(float(values["total_ms"]), 3),
                    "max_ms": round(float(values["max_ms"]), 3),
                    "mean_ms": round(
                        float(values["total_ms"]) / int(values["count"]), 3
                    ) if values["count"] else None,
                }
                for name, values in sorted(self.step_metrics.items())
            },
            "llm_steps": {
                name: dict(sorted(values.items()))
                for name, values in sorted(self.llm_step_metrics.items())
            },
            "extraction_iterations": self.extraction_iterations,
            "fallback_reply_used": self.fallback_reply_used,
            "reply_check_failures": self.reply_check_failures,
            "error_counts": dict(sorted(self.error_counts.items())),
            "estimated_cost": {
                "value": round(cost, 10) if cost is not None else None,
                "input_cost_per_million_tokens": self.input_cost_per_million,
                "output_cost_per_million_tokens": self.output_cost_per_million,
                "status": (
                    "estimated" if cost is not None else
                    "no_calls" if not self.llm_calls else
                    "not_configured" if not rates_configured else
                    "missing_usage"
                ),
                "unit": "same currency/unit as configured rates",
                "basis": "provider-reported token counts; retry attempts without usage are not included",
            },
        }


def current_metrics() -> OrderMetrics | None:
    return _current_metrics.get()


@contextmanager
def metrics_scope(metrics: OrderMetrics) -> Iterator[None]:
    token: Token = _current_metrics.set(metrics)
    try:
        yield
    finally:
        _current_metrics.reset(token)


@contextmanager
def measured_step(step: str):
    metrics = current_metrics()
    request_id = metrics.request_id if metrics else "-"
    start = time.perf_counter()
    log.info("event=step_started request_id=%s step=%s", request_id, step)
    try:
        yield
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        category = getattr(
            exc,
            "error_category",
            "database" if step == "stock_lookup" else type(exc).__name__.lower(),
        )
        if metrics:
            metrics.add_step_duration(step, duration_ms)
            if not hasattr(exc, "error_category"):
                metrics.record_error(category)
        log.error(
            "event=step_failed request_id=%s step=%s outcome=failed duration_ms=%.3f "
            "error_category=%s error_type=%s",
            request_id,
            step,
            duration_ms,
            category,
            type(exc).__name__,
        )
        raise
    else:
        duration_ms = (time.perf_counter() - start) * 1000
        if metrics:
            metrics.add_step_duration(step, duration_ms)
        log.info(
            "event=step_completed request_id=%s step=%s outcome=success duration_ms=%.3f",
            request_id,
            step,
            duration_ms,
        )
