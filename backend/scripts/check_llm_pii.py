from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Any


sys.path.append(str(Path(__file__).resolve().parents[1]))

import app.llm.client as llm_client
from app.pii.client import PiiServiceError


async def check_redact_before_gemini_and_skip_restore() -> None:
    os.environ["PII_ENABLED"] = "true"

    captured_texts: list[str] = []
    restore_calls = 0

    async def fake_redact(payload: dict[str, str]) -> tuple[str, Any]:
        assert payload["Sheet1!B1"] == "Neha Rao"
        assert payload["Sheet1!B2"] == "neha@acme.in"
        return (
            "pii-doc-1",
            {
                "Sheet1!A1": "Name",
                "Sheet1!B1": "<PERSON_a1b2>",
                "Sheet1!A2": "Email",
                "Sheet1!B2": "<EMAIL_ADDRESS_c3d4>",
            },
        )

    async def fake_gemini(
        system: str,
        user: str,
        **_: object,
    ) -> str:
        assert system == "system"
        captured_texts.append(user)
        return '{"sheets":[]}'

    async def fake_restore(document_id: str, payload: Any) -> Any:
        nonlocal restore_calls
        restore_calls += 1
        return payload

    original_redact = llm_client.pii_redact
    original_restore = llm_client.pii_restore
    original_gemini = llm_client.gemini_call
    llm_client.pii_redact = fake_redact
    llm_client.pii_restore = fake_restore
    llm_client.gemini_call = fake_gemini
    pii_document_ids: list[str] = []

    try:
        result = await llm_client.call_llm_with_pii(
            "system",
            {
                "Sheet1!A1": "Name",
                "Sheet1!B1": "Neha Rao",
                "Sheet1!A2": "Email",
                "Sheet1!B2": "neha@acme.in",
            },
            pii_document_id_callback=pii_document_ids.append,
            response_mime_type="application/json",
        )
    finally:
        llm_client.pii_redact = original_redact
        llm_client.pii_restore = original_restore
        llm_client.gemini_call = original_gemini

    assert result == '{"sheets":[]}'
    assert pii_document_ids == ["pii-doc-1"]
    assert restore_calls == 0
    assert len(captured_texts) == 1
    assert "<PERSON_a1b2>" in captured_texts[0]
    assert "<EMAIL_ADDRESS_c3d4>" in captured_texts[0]
    assert "Neha Rao" not in captured_texts[0]
    assert "neha@acme.in" not in captured_texts[0]


async def check_restore_when_placeholder_returns() -> None:
    os.environ["PII_ENABLED"] = "true"

    async def fake_redact(_: dict[str, str]) -> tuple[str, Any]:
        return "pii-doc-2", {"Sheet1!A1": "<PERSON_a1b2>"}

    async def fake_gemini(*_: object, **__: object) -> str:
        return '{"sheets":[{"name":"<PERSON_a1b2>","sections":[]}]}'

    async def fake_restore(document_id: str, payload: Any) -> Any:
        assert document_id == "pii-doc-2"
        assert payload == {"llm_output": '{"sheets":[{"name":"<PERSON_a1b2>","sections":[]}]}'}
        return {"llm_output": '{"sheets":[{"name":"Restored","sections":[]}]}'}

    original_redact = llm_client.pii_redact
    original_restore = llm_client.pii_restore
    original_gemini = llm_client.gemini_call
    llm_client.pii_redact = fake_redact
    llm_client.pii_restore = fake_restore
    llm_client.gemini_call = fake_gemini

    try:
        result = await llm_client.call_llm_with_pii("system", {"Sheet1!A1": "Neha Rao"})
    finally:
        llm_client.pii_redact = original_redact
        llm_client.pii_restore = original_restore
        llm_client.gemini_call = original_gemini

    assert result == '{"sheets":[{"name":"Restored","sections":[]}]}'


async def check_restore_when_json_escaped_placeholder_returns() -> None:
    os.environ["PII_ENABLED"] = "true"

    async def fake_redact(_: dict[str, str]) -> tuple[str, Any]:
        return "pii-doc-escaped", {"Sheet1!A1": "<ORGANIZATION_1bc2>"}

    async def fake_gemini(*_: object, **__: object) -> str:
        return '{"sheets":[{"name":"\\u003cORGANIZATION_1bc2\\u003e Information","sections":[]}]}'

    async def fake_restore(document_id: str, payload: Any) -> Any:
        assert document_id == "pii-doc-escaped"
        assert payload == {
            "llm_output": '{"sheets":[{"name":"<ORGANIZATION_1bc2> Information","sections":[]}]}'
        }
        return {"llm_output": '{"sheets":[{"name":"Lease Information","sections":[]}]}'}

    original_redact = llm_client.pii_redact
    original_restore = llm_client.pii_restore
    original_gemini = llm_client.gemini_call
    llm_client.pii_redact = fake_redact
    llm_client.pii_restore = fake_restore
    llm_client.gemini_call = fake_gemini

    try:
        result = await llm_client.call_llm_with_pii(
            "system",
            {"Sheet1!A1": "Lease Information"},
        )
    finally:
        llm_client.pii_redact = original_redact
        llm_client.pii_restore = original_restore
        llm_client.gemini_call = original_gemini

    assert result == '{"sheets":[{"name":"Lease Information","sections":[]}]}'


async def check_redact_failure_blocks_gemini() -> None:
    os.environ["PII_ENABLED"] = "true"
    gemini_calls = 0

    async def fake_redact(_: dict[str, str]) -> tuple[str, Any]:
        raise PiiServiceError("redact failed")

    async def fake_gemini(*_: object, **__: object) -> str:
        nonlocal gemini_calls
        gemini_calls += 1
        return "{}"

    original_redact = llm_client.pii_redact
    original_gemini = llm_client.gemini_call
    llm_client.pii_redact = fake_redact
    llm_client.gemini_call = fake_gemini

    try:
        try:
            await llm_client.call_llm_with_pii("system", {"Sheet1!A1": "Neha Rao"})
        except PiiServiceError:
            pass
        else:
            raise AssertionError("Expected PiiServiceError.")
    finally:
        llm_client.pii_redact = original_redact
        llm_client.gemini_call = original_gemini

    assert gemini_calls == 0


async def check_disabled_skips_pii() -> None:
    os.environ["PII_ENABLED"] = "false"
    redaction_calls = 0
    captured_texts: list[str] = []

    async def fake_redact(_: dict[str, str]) -> tuple[str, Any]:
        nonlocal redaction_calls
        redaction_calls += 1
        return "pii-doc", {}

    async def fake_gemini(_: str, user: str, **__: object) -> str:
        captured_texts.append(user)
        return "raw-ok"

    original_redact = llm_client.pii_redact
    original_gemini = llm_client.gemini_call
    llm_client.pii_redact = fake_redact
    llm_client.gemini_call = fake_gemini

    try:
        result = await llm_client.call_llm_with_pii(
            "system",
            {"Sheet1!A1": "Neha Rao"},
        )
    finally:
        llm_client.pii_redact = original_redact
        llm_client.gemini_call = original_gemini
        os.environ["PII_ENABLED"] = "true"

    assert result == "raw-ok"
    assert redaction_calls == 0
    assert "Neha Rao" in captured_texts[0]


async def main() -> None:
    await check_redact_before_gemini_and_skip_restore()
    await check_restore_when_placeholder_returns()
    await check_restore_when_json_escaped_placeholder_returns()
    await check_redact_failure_blocks_gemini()
    await check_disabled_skips_pii()
    print("LLM PII wrapper check passed.")


if __name__ == "__main__":
    asyncio.run(main())
