import asyncio
from pathlib import Path
import sys
from typing import Any

from openpyxl import Workbook


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models import Document, DocumentSchema
from app.pipeline.extract import extract_document_json, get_document_output_json
from app.pipeline.fingerprint import build_header_structure_fingerprint


BACKEND_ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BACKEND_ROOT / "uploads"
WORKBOOK_NAME = "step15-extraction.xlsx"
STORED_PATH = f"uploads/{WORKBOOK_NAME}"


HEADER_STRUCTURE = {
    "sheets": [
        {
            "name": "Original Lease - Lease",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Amendment Abstract",
                    "fields": ["Lease ID", "Type"],
                },
                {
                    "type": "table",
                    "title": "Charge Schedules",
                    "headers": [
                        {"name": "Charge Code"},
                        {"name": "Date From"},
                        {"name": "Date To"},
                        {"name": "Monthly Amt"},
                        {"name": "Annual Amt"},
                    ],
                },
            ],
        }
    ]
}


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

        for key, value in update.get("$set", {}).items():
            record[key] = value

        for key in update.get("$unset", {}):
            record.pop(key, None)

        return FakeUpdateResult(1)


class FakeDb:
    def __init__(self, document: Document, schema: DocumentSchema) -> None:
        self.documents = FakeCollection([document.model_dump(by_alias=True)])
        self.schemas = FakeCollection([schema.model_dump(by_alias=True)])


def create_workbook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Original Lease - Lease"

    # Repeated cells mimic the expanded merged-cell grid used by the loader.
    worksheet.append(["Amendment Abstract", "Amendment Abstract"])
    worksheet.append(["Lease ID", "Lease ID", "t0003946", "t0003946"])
    worksheet.append(["Type", "Type", "Original Lease", "Original Lease"])
    worksheet.append([])
    worksheet.append(["Charge Schedules", "Charge Schedules"])
    worksheet.append(
        [
            "Charge Code",
            "Charge Code",
            "Date From",
            "Date From",
            "Date To",
            "Date To",
            "Monthly Amt",
            "Monthly Amt",
            "Annual Amt",
            "Annual Amt",
        ]
    )
    worksheet.append(
        [
            "RENT",
            "RENT",
            "2026-01-01",
            "2026-01-01",
            "2026-12-31",
            "2026-12-31",
            1000,
            1000,
            12000,
            12000,
        ]
    )
    worksheet.append(
        [
            "CAM",
            "CAM",
            "2026-01-01",
            "2026-01-01",
            "2026-12-31",
            "2026-12-31",
            250,
            250,
            3000,
            3000,
        ]
    )

    workbook.save(path)


async def main() -> None:
    workbook_path = UPLOADS_DIR / WORKBOOK_NAME
    create_workbook(workbook_path)

    schema = DocumentSchema(
        name="Dollar Tree extraction layout",
        file_type="xlsx",
        fingerprint=build_header_structure_fingerprint(HEADER_STRUCTURE),
        header_structure=HEADER_STRUCTURE,
    )
    document = Document(
        filename=WORKBOOK_NAME,
        file_type="xlsx",
        stored_path=STORED_PATH,
        status="approved",
        fingerprint=schema.fingerprint,
        matched_schema_id=schema.id,
    )
    fake_db = FakeDb(document, schema)

    try:
        first_result = await extract_document_json(document.id, db=fake_db)
        second_result = await extract_document_json(document.id, db=fake_db)
        stored_json_result = await get_document_output_json(document.id, db=fake_db)
    finally:
        workbook_path.unlink(missing_ok=True)

    output_json = first_result["output_json"]
    sheet_json = output_json["Original Lease - Lease"]

    assert first_result["locators_created"] is True
    assert first_result["status"] == "extracted"
    assert second_result["locators_created"] is False
    assert stored_json_result["status"] == "extracted"
    assert stored_json_result["output_json"] == output_json
    assert fake_db.schemas.records[schema.id]["field_locators"]["version"] == 1
    assert fake_db.documents.records[document.id]["status"] == "extracted"
    assert fake_db.documents.records[document.id]["output_json"] == output_json
    assert sheet_json["Amendment Abstract"] == {
        "Lease ID": "t0003946",
        "Type": "Original Lease",
    }
    assert sheet_json["Charge Schedules"] == [
        {
            "Charge Code": "RENT",
            "Date From": "2026-01-01",
            "Date To": "2026-12-31",
            "Monthly Amt": 1000,
            "Annual Amt": 12000,
        },
        {
            "Charge Code": "CAM",
            "Date From": "2026-01-01",
            "Date To": "2026-12-31",
            "Monthly Amt": 250,
            "Annual Amt": 3000,
        },
    ]

    print("Excel extraction check passed.")
    print("Charge Schedules rows:", len(sheet_json["Charge Schedules"]))
    print("Locators created first run:", first_result["locators_created"])
    print("Locators reused second run:", not second_result["locators_created"])


if __name__ == "__main__":
    asyncio.run(main())
