from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any

from openpyxl import Workbook


sys.path.append(str(Path(__file__).resolve().parents[1]))

import app.llm.client as llm_client
from app.models import Document
from app.pii.adapt import contains_placeholder
from app.pipeline.detect import detect_document_headers


BACKEND_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BACKEND_ROOT / "uploads"
WORKBOOK_NAME = "pii-detect-smoke.xlsx"
STORED_PATH = f"uploads/{WORKBOOK_NAME}"


class FakeUpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeCollection:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = {record["_id"]: record for record in records}

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        record_id = query.get("_id")
        if record_id is None:
            return None

        return self.records.get(record_id)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
    ) -> FakeUpdateResult:
        record = await self.find_one(query)
        if record is None:
            return FakeUpdateResult(0)

        status_filter = query.get("status")
        if isinstance(status_filter, dict) and status_filter.get("$ne") == record.get("status"):
            return FakeUpdateResult(0)

        for key, value in update.get("$set", {}).items():
            record[key] = value

        for key in update.get("$unset", {}):
            record.pop(key, None)

        return FakeUpdateResult(1)


class FakeDb:
    def __init__(self, document: Document) -> None:
        self.documents = FakeCollection([document.model_dump(by_alias=True)])
        self.schemas = FakeCollection([])


def create_workbook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet.append(["Contact", "Contact"])
    worksheet.append(["Name", "Neha Rao"])
    worksheet.append(["Email", "neha@acme.in"])
    worksheet.append([])
    worksheet.append(["Charge Schedule", "Charge Schedule"])
    worksheet.append(["Code", "Amount"])
    worksheet.append(["BASE", 100])
    workbook.save(path)


async def main() -> None:
    workbook_path = UPLOADS_DIR / WORKBOOK_NAME
    create_workbook(workbook_path)

    document = Document(
        filename=WORKBOOK_NAME,
        file_type="xlsx",
        stored_path=STORED_PATH,
    )
    fake_db = FakeDb(document)
    captured_prompts: list[str] = []

    async def fake_gemini(_: str, user: str, **__: object) -> str:
        captured_prompts.append(user)
        return (
            '{"sheets":[{"name":"Sheet1","sections":['
            '{"type":"key_value","title":"Contact","fields":["Name","Email"]},'
            '{"type":"table","title":"Charge Schedule","headers":['
            '{"name":"Code"},{"name":"Amount"}]}'
            "]}]}"
        )

    original_gemini = llm_client.gemini_call
    llm_client.gemini_call = fake_gemini

    try:
        result = await detect_document_headers(document.id, db=fake_db)
    finally:
        llm_client.gemini_call = original_gemini
        workbook_path.unlink(missing_ok=True)

    stored_document = fake_db.documents.records[document.id]
    assert result["source"] == "llm"
    assert stored_document["status"] == "needs_review"
    assert stored_document["pii_document_id"]
    assert captured_prompts
    assert "Neha Rao" not in captured_prompts[0]
    assert "neha@acme.in" not in captured_prompts[0]
    assert contains_placeholder(captured_prompts[0])

    print("Detect PII pipeline check passed.")
    print("PII document id stored:", bool(stored_document["pii_document_id"]))


if __name__ == "__main__":
    asyncio.run(main())
