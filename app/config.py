"""All settings come from environment variables (.env). Nothing is hardcoded."""
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    azure_endpoint: str
    azure_api_key: str
    azure_api_version: str
    azure_deployment: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    lead_time_days: int
    extraction_max_iterations: int
    llm_timeout_s: float
    llm_max_attempts: int
    log_level: str
    llm_input_cost_per_million_tokens: float | None = None
    llm_output_cost_per_million_tokens: float | None = None


def _optional_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return parsed


@lru_cache
def get_settings() -> Settings:
    return Settings(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
        azure_api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
        azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", ""),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", ""),
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=int(os.getenv("POSTGRES_PORT", "5433")),
        db_user=os.getenv("POSTGRES_USER", "orders"),
        db_password=os.getenv("POSTGRES_PASSWORD", "orders"),
        db_name=os.getenv("POSTGRES_DB", "orders"),
        lead_time_days=int(os.getenv("LEAD_TIME_DAYS", "3")),
        extraction_max_iterations=int(os.getenv("EXTRACTION_MAX_ITERATIONS", "5")),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60")),
        llm_max_attempts=int(os.getenv("LLM_MAX_ATTEMPTS", "4")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        llm_input_cost_per_million_tokens=_optional_float("LLM_INPUT_COST_PER_1M_TOKENS"),
        llm_output_cost_per_million_tokens=_optional_float("LLM_OUTPUT_COST_PER_1M_TOKENS"),
    )
