import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(BACKEND_ROOT / ".env")

DEFAULT_LLM_MODEL = "gemini-2.5-flash"
DEFAULT_LLM_TEMPERATURE = 0.0
DEFAULT_LLM_MAX_RETRIES = 2
DEFAULT_LLM_RETRY_BASE_SECONDS = 1.0


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


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number.") from error
