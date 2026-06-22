import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(BACKEND_ROOT / ".env")

DEFAULT_LLM_MODEL = "gemini-3.1-pro-preview"
DEFAULT_LLM_TEMPERATURE = 0.0
DEFAULT_LLM_MAX_RETRIES = 2
DEFAULT_LLM_RETRY_BASE_SECONDS = 1.0
DEFAULT_PII_BASE_URL = "http://124.123.18.150:9090/pii/api"
DEFAULT_PII_TIMEOUT_SECONDS = 30.0


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Add it to backend/.env.")

    return value


def get_llm_model() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_LLM_MODEL)


def get_llm_max_retries() -> int:
    return _get_int_env("LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES)


def get_llm_retry_base_seconds() -> float:
    return _get_float_env("LLM_RETRY_BASE_SECONDS", DEFAULT_LLM_RETRY_BASE_SECONDS)


def get_pii_enabled() -> bool:
    return _get_bool_env("PII_ENABLED", True)


def get_pii_base_url() -> str:
    return os.getenv("PII_BASE_URL", DEFAULT_PII_BASE_URL).rstrip("/")


def get_pii_api_key() -> str:
    return os.getenv("PII_API_KEY", "")


def get_pii_service_id() -> str:
    return os.getenv("PII_SERVICE_ID", "")


def get_pii_timeout_seconds() -> float:
    return _get_float_env("PII_TIMEOUT_SECONDS", DEFAULT_PII_TIMEOUT_SECONDS)


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise RuntimeError(f"{name} must be a boolean.")


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number.") from error


if get_pii_enabled() and not get_pii_api_key():
    raise RuntimeError("PII_API_KEY is required when PII_ENABLED=true")
