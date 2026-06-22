from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PII_BASE_URL = "http://124.123.18.150:9090/pii/api"
PLACEHOLDER_RE_TEXT = r"<[A-Z_]+_[A-Fa-f0-9]{4,}>"


async def main() -> None:
    load_dotenv(BACKEND_ROOT / ".env")

    base_url = os.getenv("PII_BASE_URL", DEFAULT_PII_BASE_URL).rstrip("/")
    api_key = os.getenv("PII_API_KEY", "")
    timeout = float(os.getenv("PII_TIMEOUT_SECONDS", "30"))

    async with httpx.AsyncClient(timeout=timeout) as client:
        if not api_key:
            response = await client.post(
                f"{base_url}/admin/keys/generate",
                json={
                    "service_name": "xlsx-extractor",
                    "owner_email": "shiva@nervesparks.in",
                },
            )
            response.raise_for_status()
            body = response.json()
            plain_text_api_key = body.get("plain_text_api_key")
            if not plain_text_api_key:
                raise RuntimeError("Key generation response missing plain_text_api_key.")

            print("Generated PII API key. Paste this into backend/.env:")
            print(f"PII_API_KEY={plain_text_api_key}")
            print("Then rerun: python scripts/pii_smoke.py")
            return

        health = await client.get(f"{base_url}/health")
        health.raise_for_status()

        original = {"name": "John Doe", "email": "john@x.com"}
        redact = await client.post(
            f"{base_url}/api/v1/process-json/redact",
            headers={"x-api-key": api_key},
            json=original,
        )
        redact.raise_for_status()
        redact_body = redact.json()
        document_id = redact_body.get("document_id")
        redacted_data = redact_body.get("data")
        if not document_id or not isinstance(redacted_data, dict):
            raise RuntimeError("Redact response missing document_id or data.")

        if redacted_data == original:
            raise RuntimeError("Redact response did not mask any values.")

        restore = await client.post(
            f"{base_url}/api/v1/process-json/restore/{document_id}",
            headers={"x-api-key": api_key},
            json=redacted_data,
        )
        restore.raise_for_status()
        restore_body = restore.json()
        if restore_body.get("data") != original:
            raise RuntimeError("Restore response did not round-trip to original input.")

    print("PII round-trip OK")
    print(f"Placeholder regex: {PLACEHOLDER_RE_TEXT}")


if __name__ == "__main__":
    asyncio.run(main())
