"""The only place that touches the OpenAI SDK. Handles timeout, retry with backoff, and logging."""
import logging
import random
import time
from typing import Callable, TypeVar

import openai
from openai import AzureOpenAI
from pydantic import BaseModel

from app.config import Settings
from app.observability import current_metrics

log = logging.getLogger("llm")
T = TypeVar("T", bound=BaseModel)

RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class LLMError(Exception):
    """The LLM call failed after all retries, or returned unusable output."""

    def __init__(self, message: str, error_category: str = "llm_error"):
        super().__init__(message)
        self.error_category = error_category


class LLMClient:
    def __init__(self, settings: Settings, usage_callback: Callable[[dict[str, int]], None] | None = None):
        missing = [
            k
            for k, v in {
                "AZURE_OPENAI_ENDPOINT": settings.azure_endpoint,
                "AZURE_OPENAI_API_KEY": settings.azure_api_key,
                "AZURE_OPENAI_API_VERSION": settings.azure_api_version,
                "AZURE_OPENAI_DEPLOYMENT": settings.azure_deployment,
            }.items()
            if not v
        ]
        if missing:
            raise RuntimeError(f"Missing settings in .env: {', '.join(missing)}")
        self._deployment = settings.azure_deployment
        self._azure_embedding_deployment = settings.azure_embedding_deployment
        self._max_attempts = settings.llm_max_attempts
        self._usage_callback = usage_callback
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_key=settings.azure_api_key,
            api_version=settings.azure_api_version,
            timeout=settings.llm_timeout_s,
            max_retries=0,  # we retry ourselves so every attempt is logged
        )

    def _call(self, step: str, fn):
        metrics = current_metrics()
        call_id = metrics.start_llm_call() if metrics else 0
        request_id = metrics.request_id if metrics else "-"
        for attempt in range(1, self._max_attempts + 1):
            start = time.perf_counter()
            try:
                resp = fn()
            except RETRYABLE as e:
                ms = (time.perf_counter() - start) * 1000
                category = self._error_category(e)
                if metrics:
                    metrics.record_llm_attempt(call_id, step, attempt, "failed", ms, category)
                if attempt == self._max_attempts:
                    log.error(
                        "event=llm_call_failed request_id=%s step=%s attempt=%d "
                        "error_category=%s error_type=%s",
                        request_id, step, attempt, category, type(e).__name__,
                    )
                    raise LLMError(
                        f"{step}: LLM unavailable after {attempt} attempts ({type(e).__name__})",
                        category,
                    ) from e
                wait = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                log.warning(
                    "event=llm_retry request_id=%s step=%s attempt=%d error_category=%s "
                    "duration_ms=%.3f delay_s=%.3f",
                    request_id, step, attempt, category, ms, wait,
                )
                time.sleep(wait)
                continue
            except openai.APIError as e:  # 400/401/404 etc: retrying won't help
                ms = (time.perf_counter() - start) * 1000
                category = self._error_category(e)
                if metrics:
                    metrics.record_llm_attempt(call_id, step, attempt, "failed", ms, category)
                log.error(
                    "event=llm_call_failed request_id=%s step=%s attempt=%d "
                    "error_category=%s error_type=%s",
                    request_id, step, attempt, category, type(e).__name__,
                )
                raise LLMError(f"{step}: {type(e).__name__}", category) from e
            except Exception:
                ms = (time.perf_counter() - start) * 1000
                if metrics:
                    metrics.record_llm_attempt(call_id, step, attempt, "failed", ms, "unexpected_error")
                log.error(
                    "event=llm_call_failed request_id=%s step=%s attempt=%d "
                    "error_category=unexpected_error",
                    request_id, step, attempt,
                )
                raise

            ms = (time.perf_counter() - start) * 1000
            u = resp.usage
            usage = {}
            if self._usage_callback and u:
                for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = getattr(u, field, None)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[field] = value
                if usage:
                    self._usage_callback(usage)
            if u:
                for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    value = getattr(u, field, None)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage[field] = value
            if metrics:
                metrics.record_llm_attempt(call_id, step, attempt, "success", ms, usage=usage)
            log.info(
                "event=llm_call_completed request_id=%s step=%s model=%s duration_ms=%.3f "
                "prompt_tokens=%s completion_tokens=%s total_tokens=%s attempt=%d",
                request_id, step, resp.model, ms,
                getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None),
                getattr(u, "total_tokens", None), attempt,
            )
            return resp

    @staticmethod
    def _error_category(error: openai.APIError) -> str:
        if isinstance(error, openai.RateLimitError):
            return "rate_limit"
        if isinstance(error, openai.APITimeoutError):
            return "timeout"
        if isinstance(error, openai.APIConnectionError):
            return "connection"
        if isinstance(error, openai.InternalServerError):
            return "server"
        if isinstance(error, openai.AuthenticationError):
            return "authentication"
        if isinstance(error, openai.BadRequestError):
            return "invalid_request"
        if isinstance(error, openai.APIStatusError):
            if error.status_code in (400, 422):
                return "invalid_request"
            if error.status_code == 401:
                return "authentication"
            if error.status_code == 403:
                return "permission_denied"
            if error.status_code == 404:
                return "not_found"
            if error.status_code == 409:
                return "conflict"
            if error.status_code >= 500:
                return "server"
            return f"http_{error.status_code}"
        return "api_error"

    def parse(self, step: str, messages: list[dict], schema: type[T], max_completion_tokens: int = 4000) -> T:
        """Structured output: returns an instance of `schema`, validated by Pydantic."""
        resp = self._call(
            step,
            lambda: self._client.chat.completions.parse(
                model=self._deployment,
                messages=messages,
                response_format=schema,
                max_completion_tokens=max_completion_tokens,
            ),
        )
        msg = resp.choices[0].message
        if msg.refusal:
            self._record_output_error(step, "model_refusal")
            raise LLMError(f"{step}: model refused", "model_refusal")
        if msg.parsed is None:
            self._record_output_error(step, "unparsable_output")
            raise LLMError(
                f"{step}: no parsable output (finish_reason={resp.choices[0].finish_reason})",
                "unparsable_output",
            )
        return schema.model_validate(msg.parsed.model_dump())  # validate again, as the guide asks

    def complete(self, step: str, messages: list[dict], max_completion_tokens: int = 2000) -> str:
        resp = self._call(
            step,
            lambda: self._client.chat.completions.create(
                model=self._deployment,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
            ),
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            self._record_output_error(step, "empty_output")
            raise LLMError(
                f"{step}: empty response (finish_reason={resp.choices[0].finish_reason})",
                "empty_output",
            )
        return text

    def embed(self, text: str) -> list[float]:
        """Create a 1536-dimension embedding for RAG."""
        if not self._azure_embedding_deployment:
            raise LLMError("embedding deployment is not configured", "configuration")
        response = self._call(
            "embedding",
            lambda: self._client.embeddings.create(
                model=self._azure_embedding_deployment,
                input=text,
            ),
        )
        return [float(value) for value in response.data[0].embedding]

    @staticmethod
    def _record_output_error(step: str, category: str) -> None:
        metrics = current_metrics()
        if metrics:
            metrics.record_error(category)
            log.error(
                "event=llm_output_failed request_id=%s step=%s error_category=%s",
                metrics.request_id,
                step,
                category,
            )
