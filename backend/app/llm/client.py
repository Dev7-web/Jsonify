from __future__ import annotations

import asyncio
import time
from typing import Final

from google import genai
from google.genai import errors, types

from app.config import (
    DEFAULT_LLM_TEMPERATURE,
    get_llm_max_retries,
    get_llm_model,
    get_llm_retry_base_seconds,
    get_required_env,
)


TRANSIENT_STATUS_CODES: Final[set[int]] = {408, 429, 500, 502, 503, 504}

_client: genai.Client | None = None


class LLMConfigurationError(RuntimeError):
    pass


class LLMCallError(RuntimeError):
    pass


def call_llm(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = DEFAULT_LLM_TEMPERATURE,
    max_retries: int | None = None,
    response_mime_type: str | None = None,
) -> str:
    """Call Gemini synchronously. Use from scripts and sync code only."""
    _validate_prompts(system, user)
    return _call_with_retry_sync(
        client=_get_client(),
        system=system,
        user=user,
        model=model or get_llm_model(),
        temperature=temperature,
        max_retries=get_llm_max_retries() if max_retries is None else max_retries,
        response_mime_type=response_mime_type,
    )


async def acall_llm(
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = DEFAULT_LLM_TEMPERATURE,
    max_retries: int | None = None,
    response_mime_type: str | None = None,
) -> str:
    """Async sibling of `call_llm`. Use from FastAPI route handlers so retry
    sleeps don't block the event loop."""
    _validate_prompts(system, user)
    return await _call_with_retry_async(
        client=_get_client(),
        system=system,
        user=user,
        model=model or get_llm_model(),
        temperature=temperature,
        max_retries=get_llm_max_retries() if max_retries is None else max_retries,
        response_mime_type=response_mime_type,
    )


def _validate_prompts(system: str, user: str) -> None:
    if not system.strip():
        raise ValueError("system prompt must not be empty.")

    if not user.strip():
        raise ValueError("user prompt must not be empty.")


def _get_client() -> genai.Client:
    global _client

    if _client is None:
        try:
            api_key = get_required_env("GEMINI_API_KEY")
        except RuntimeError as error:
            raise LLMConfigurationError(str(error)) from error

        _client = genai.Client(api_key=api_key)

    return _client


def _build_config(
    system: str,
    temperature: float,
    response_mime_type: str | None,
) -> types.GenerateContentConfig:
    kwargs: dict[str, object] = {
        "system_instruction": system,
        "temperature": temperature,
    }
    if response_mime_type is not None:
        kwargs["response_mime_type"] = response_mime_type
    return types.GenerateContentConfig(**kwargs)


def _call_with_retry_sync(
    *,
    client: genai.Client,
    system: str,
    user: str,
    model: str,
    temperature: float,
    max_retries: int,
    response_mime_type: str | None,
) -> str:
    retry_base_seconds = get_llm_retry_base_seconds()
    config = _build_config(system, temperature, response_mime_type)

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user,
                config=config,
            )
            return _extract_text(response)
        except errors.APIError as error:
            if not _should_retry_api_error(error, attempt, max_retries):
                raise LLMCallError(_format_api_error(error)) from error
            time.sleep(_retry_delay_seconds(retry_base_seconds, attempt))
        except (TimeoutError, ConnectionError) as error:
            if attempt >= max_retries:
                raise LLMCallError(f"Gemini request failed: {error}") from error
            time.sleep(_retry_delay_seconds(retry_base_seconds, attempt))

    raise LLMCallError("Gemini request failed after retries.")


async def _call_with_retry_async(
    *,
    client: genai.Client,
    system: str,
    user: str,
    model: str,
    temperature: float,
    max_retries: int,
    response_mime_type: str | None,
) -> str:
    retry_base_seconds = get_llm_retry_base_seconds()
    config = _build_config(system, temperature, response_mime_type)

    for attempt in range(max_retries + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=user,
                config=config,
            )
            return _extract_text(response)
        except errors.APIError as error:
            if not _should_retry_api_error(error, attempt, max_retries):
                raise LLMCallError(_format_api_error(error)) from error
            await asyncio.sleep(_retry_delay_seconds(retry_base_seconds, attempt))
        except (TimeoutError, ConnectionError) as error:
            if attempt >= max_retries:
                raise LLMCallError(f"Gemini request failed: {error}") from error
            await asyncio.sleep(_retry_delay_seconds(retry_base_seconds, attempt))

    raise LLMCallError("Gemini request failed after retries.")


def _extract_text(response: object) -> str:
    # Inspect finish_reason before reading .text so we surface SAFETY/MAX_TOKENS
    # failures explicitly instead of as a generic "no text" error.
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish_reason = getattr(candidates[0], "finish_reason", None)
        reason_name = getattr(finish_reason, "name", None) or str(finish_reason)

        if reason_name == "SAFETY":
            raise LLMCallError("Gemini blocked the response: content filter (SAFETY).")
        if reason_name == "MAX_TOKENS":
            raise LLMCallError(
                "Gemini response truncated at max_tokens. "
                "Increase the limit or shorten the prompt."
            )
        if reason_name == "RECITATION":
            raise LLMCallError("Gemini blocked the response: RECITATION.")

    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise LLMCallError("Gemini response did not include text.")

    return text.strip()


def _should_retry_api_error(
    error: errors.APIError,
    attempt: int,
    max_retries: int,
) -> bool:
    if attempt >= max_retries:
        return False

    return getattr(error, "code", None) in TRANSIENT_STATUS_CODES


def _format_api_error(error: errors.APIError) -> str:
    code = getattr(error, "code", None)
    message = getattr(error, "message", str(error))

    if code is None:
        return f"Gemini request failed: {message}"

    return f"Gemini request failed with status {code}: {message}"


def _retry_delay_seconds(retry_base_seconds: float, attempt: int) -> float:
    return retry_base_seconds * (2**attempt)
