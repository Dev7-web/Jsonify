from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

import httpx

from app.config import (
    get_pii_api_key,
    get_pii_base_url,
    get_pii_timeout_seconds,
)


PLACEHOLDER_RE = re.compile(r"<[A-Z_]+_[A-Fa-f0-9]{4,}>")

_RETRY_DELAY_SECONDS = 1.0


class PiiServiceError(RuntimeError):
    pass


async def pii_health() -> bool:
    try:
        response = await _request("GET", "/health", auth=False)
    except PiiServiceError:
        return False

    return response.status_code == 200


async def pii_redact(payload: dict[str, Any] | list[Any]) -> tuple[str, Any]:
    response = await _request(
        "POST",
        "/api/v1/process-json/redact",
        auth=True,
        json_payload=payload,
    )
    body = _response_json(response)
    document_id = body.get("document_id")
    data = body.get("data")
    if not isinstance(document_id, str) or not document_id:
        raise PiiServiceError("PII redact response missing document_id.")

    return document_id, data


async def pii_restore(document_id: str, payload: Any) -> Any:
    if not document_id.strip():
        raise PiiServiceError("PII restore document_id must not be empty.")

    response = await _request(
        "POST",
        f"/api/v1/process-json/restore/{document_id}",
        auth=True,
        json_payload=payload,
    )
    body = _response_json(response)
    if "data" not in body:
        raise PiiServiceError("PII restore response missing data.")

    return body["data"]


async def _request(
    method: Literal["GET", "POST"],
    path: str,
    *,
    auth: bool,
    json_payload: Any | None = None,
) -> httpx.Response:
    base_url = get_pii_base_url()
    timeout = get_pii_timeout_seconds()
    headers: dict[str, str] = {}
    if auth:
        api_key = get_pii_api_key()
        if not api_key:
            raise PiiServiceError("PII_API_KEY is required for PII service calls.")
        headers["x-api-key"] = api_key

    url = f"{base_url}{path}"
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=json_payload,
                )
        except httpx.TimeoutException as error:
            last_error = error
            if attempt == 0:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                continue
            raise PiiServiceError("PII service request timed out.") from error
        except httpx.HTTPError as error:
            raise PiiServiceError(f"PII service request failed: {error}") from error

        if response.status_code < 400:
            return response

        if response.status_code >= 500 and attempt == 0:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            continue

        raise PiiServiceError(_format_response_error(response))

    if last_error is not None:
        raise PiiServiceError("PII service request failed after retry.") from last_error

    raise PiiServiceError("PII service request failed after retry.")


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise PiiServiceError("PII service response was not valid JSON.") from error

    if not isinstance(body, dict):
        raise PiiServiceError("PII service response must be a JSON object.")

    return body


def _format_response_error(response: httpx.Response) -> str:
    body = response.text
    if len(body) > 500:
        body = f"{body[:500]}..."

    return f"PII service request failed with status {response.status_code}: {body}"
