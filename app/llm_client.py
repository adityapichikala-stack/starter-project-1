"""The only place that touches the OpenAI SDK. Handles timeout, retry with backoff, and logging."""
import logging
import random
import time
from typing import TypeVar

import openai
from openai import AzureOpenAI
from pydantic import BaseModel

from app.config import Settings

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


class LLMClient:
    def __init__(self, settings: Settings):
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
        self._max_attempts = settings.llm_max_attempts
        self._client = AzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_key=settings.azure_api_key,
            api_version=settings.azure_api_version,
            timeout=settings.llm_timeout_s,
            max_retries=0,  # we retry ourselves so every attempt is logged
        )

    def _call(self, step: str, fn):
        for attempt in range(1, self._max_attempts + 1):
            start = time.perf_counter()
            try:
                resp = fn()
            except RETRYABLE as e:
                ms = (time.perf_counter() - start) * 1000
                if attempt == self._max_attempts:
                    log.error("step=%s attempt=%d failed, giving up: %s", step, attempt, e)
                    raise LLMError(f"{step}: LLM unavailable after {attempt} attempts ({type(e).__name__})") from e
                wait = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                log.warning("step=%s attempt=%d %s after %.0fms, retrying in %.1fs", step, attempt, type(e).__name__, ms, wait)
                time.sleep(wait)
                continue
            except openai.APIError as e:  # 400/401/404 etc: retrying won't help
                log.error("step=%s non-retryable error: %s", step, e)
                raise LLMError(f"{step}: {type(e).__name__}: {e}") from e

            ms = (time.perf_counter() - start) * 1000
            u = resp.usage
            log.info(
                "step=%s model=%s latency_ms=%.0f prompt_tokens=%s completion_tokens=%s total_tokens=%s attempt=%d",
                step, resp.model, ms,
                getattr(u, "prompt_tokens", None), getattr(u, "completion_tokens", None),
                getattr(u, "total_tokens", None), attempt,
            )
            return resp

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
            raise LLMError(f"{step}: model refused: {msg.refusal}")
        if msg.parsed is None:
            raise LLMError(f"{step}: no parsable output (finish_reason={resp.choices[0].finish_reason})")
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
            raise LLMError(f"{step}: empty response (finish_reason={resp.choices[0].finish_reason})")
        return text
