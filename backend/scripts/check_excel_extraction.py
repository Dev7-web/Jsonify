import asyncio
from pathlib import Path
import sys
from typing import Any

from openpyxl import Workbook


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models import Document, DocumentSchema
from app.llm.header_prompt import validate_header_structure
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
                {
                    "type": "key_value",
                    "title": "REBO Comment",
                    "fields": [],
                },
            ],
        },
        {
            "name": "Tenant Contact",
            "sections": [
                {
                    "type": "matrix",
                    "title": "LEASE/CONTACT INFORMATION:",
                    "headers": [
                        {"name": "Billing Contact"},
                        {"name": "Notice Address"},
                        {"name": "REBO Comment"},
                    ],
                }
            ],
        },
        {
            "name": "Annual Matrix",
            "sections": [
                {
                    "type": "matrix",
                    "title": "Annual Charges",
                    "row_header": "Charge Type",
                    "headers": [
                        {"name": "2024"},
                        {"name": "2025"},
                        {"name": "2026"},
                    ],
                }
            ],
        },
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
    def __init__(
        self,
        documents: Document | list[Document],
        schemas: DocumentSchema | list[DocumentSchema],
    ) -> None:
        document_list = documents if isinstance(documents, list) else [documents]
        schema_list = schemas if isinstance(schemas, list) else [schemas]
        self.documents = FakeCollection(
            [document.model_dump(by_alias=True) for document in document_list]
        )
        self.schemas = FakeCollection(
            [schema.model_dump(by_alias=True) for schema in schema_list]
        )


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
    worksheet["K1"] = "REBO Comment"
    worksheet["K2"] = "Lease ID reviewed from source document"
    worksheet["K7"] = "Rent row needs legal review"
    worksheet["K8"] = "CAM row confirmed"

    contact_sheet = workbook.create_sheet("Tenant Contact")
    contact_sheet.merge_cells("A1:O1")
    contact_sheet["A1"] = "OCCUPANT INFORMATION (TT CONTACT INFO)"
    contact_sheet.merge_cells("B3:D3")
    contact_sheet["B3"] = "LEASE/CONTACT INFORMATION:"
    contact_sheet.merge_cells("E4:G4")
    contact_sheet["E4"] = "Billing Contact"
    contact_sheet.merge_cells("H4:J4")
    contact_sheet["H4"] = "Notice Address"
    contact_sheet.merge_cells("M4:N4")
    contact_sheet["M4"] = "REBO Comment"

    contact_sheet["B5"] = "Contact Name:"
    contact_sheet["E5"] = "{{Contact Name_subsummarization}}"
    contact_sheet["B6"] = "Phone Number:"
    contact_sheet["H6"] = "757-321-5000"
    contact_sheet.merge_cells("M6:R7")
    contact_sheet["M6"] = "1. Updated Billing contact"
    contact_sheet["B7"] = "Fax Number:"
    contact_sheet["H7"] = "757-321-5220"
    contact_sheet["B8"] = "Email Address:"
    contact_sheet["E8"] = "MS_Rents@dollartree.com"
    contact_sheet["B9"] = "Billing Name:"
    contact_sheet["E9"] = "Dollar Tree Stores, Inc."
    contact_sheet["H9"] = "Dollar Tree Stores, Inc."
    contact_sheet["B10"] = "Attention:"
    contact_sheet["E10"] = "Lease Accounting Dept 300"
    contact_sheet["H10"] = "Lease Administration Department"
    contact_sheet["B11"] = "Address:"
    contact_sheet["E11"] = "500 Volvo Parkway"
    contact_sheet["H11"] = "500 Volvo Parkway"
    contact_sheet["B12"] = "City State Zip:"
    contact_sheet["E12"] = "Chesapeake, VA 23320"
    contact_sheet["H12"] = "Chesapeake, VA 23320"

    annual_sheet = workbook.create_sheet("Annual Matrix")
    annual_sheet["A1"] = "Annual Charges"
    annual_sheet["B2"] = "2024"
    annual_sheet["C2"] = "2025"
    annual_sheet["D2"] = "2026"
    annual_sheet["A3"] = "CAM"
    annual_sheet["B3"] = 100
    annual_sheet["C3"] = 110
    annual_sheet["D3"] = 120
    annual_sheet["A4"] = "Tax"
    annual_sheet["B4"] = 20
    annual_sheet["C4"] = 25
    annual_sheet["D4"] = 30

    workbook.save(path)


PACIFIC_HEADER_STRUCTURE = {
    "sheets": [
        {
            "name": "Lease_Abstract (1)",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Lease_Abstract (1) section 1",
                    "fields": ["Tenant:", "Landlord:"],
                },
                {
                    "type": "key_value",
                    "title": "Property Information:",
                    "fields": [
                        "Address 2 :",
                        "Zip :",
                        "Property Name :",
                        "Suite Store :",
                        "Country :",
                        "Currency :",
                    ],
                },
                {
                    "type": "table",
                    "title": "Term Information:",
                    "headers": [
                        {"name": "Description"},
                        {"name": "Lease Commencement"},
                        {"name": "Rent Commencement"},
                        {"name": "Expiration"},
                        {"name": "Term"},
                        {"name": "Cite"},
                    ],
                },
                {
                    "type": "table",
                    "title": "Rent Schedule:",
                    "headers": [
                        {"name": "Rent Type"},
                        {"name": "Begin Date"},
                        {"name": "End Date"},
                        {"name": "Monthly"},
                        {"name": "Annual"},
                        {"name": "SF"},
                        {"name": "PSF/Year"},
                        {"name": "Cite"},
                    ],
                },
                {
                    "type": "table",
                    "title": "Expense Recoveries - CAM:",
                    "headers": [
                        {"name": "Begin Date"},
                        {"name": "End Date"},
                        {"name": "Type"},
                        {"name": "Pro-rata share"},
                        {"name": "Base Year"},
                        {"name": "Cap"},
                    ],
                },
                {
                    "type": "key_value",
                    "title": "Lease_Abstract (1) section 9",
                    "fields": ["Notes :"],
                },
                {
                    "type": "table",
                    "title": "Termination Options:",
                    "headers": [
                        {"name": "Description"},
                        {"name": "First Notice Date"},
                        {"name": "Last Notice Date"},
                        {"name": "Notice Period"},
                        {"name": "Fee"},
                    ],
                },
                {
                    "type": "key_value",
                    "title": "Lease_Abstract (1) section 19",
                    "fields": [
                        "Termination Notes :",
                        "First Offer or Refusal :",
                        "Purchase :",
                        "Relocation :",
                    ],
                },
            ],
        }
    ]
}


def create_pacific_workbook(
    path: Path,
    *,
    lease_title: str,
    tenant: str,
    landlord: str,
    zip_code: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Lease_Abstract (1)"

    worksheet.append(
        [
            None,
            lease_title,
            lease_title,
            lease_title,
            lease_title,
            lease_title,
            "Tenant:",
            tenant,
            tenant,
        ]
    )
    worksheet.append(
        [
            None,
            lease_title,
            lease_title,
            lease_title,
            lease_title,
            lease_title,
            "Landlord:",
            landlord,
            landlord,
        ]
    )
    worksheet.append([])
    worksheet.append(
        [
            None,
            "Property Information:",
            "Property Information:",
            "Property Information:",
            "Property Information:",
            "Property Information:",
        ]
    )
    worksheet.append([None, "Address 2 :", None, None, None, None, "Zip :", zip_code])
    # Single placeholder -> null.
    worksheet.append([None, "Property Name :", "{{Property Name_subsummarization}}"])
    # Two placeholders joined by a separator -> null.
    worksheet.append(
        [
            None,
            "Suite Store :",
            "{{Suite Store_subsection no}}: {{Suite Store_subsummarization}}",
        ]
    )
    # Multiline placeholders (loader collapses the newline) -> null.
    worksheet.append(
        [
            None,
            "Country :",
            "{{Country Line One_subsummarization}}\n{{Country Line Two_subsummarization}}",
        ]
    )
    # Mixed real text + placeholder -> preserved unchanged.
    worksheet.append(
        [None, "Currency :", "Article 12 - {{Currency Notes_subsummarization}}"]
    )
    worksheet.append([])
    worksheet.append(
        [
            None,
            "Term Information:",
            "Term Information:",
            "Term Information:",
            "Term Information:",
            "Term Information:",
        ]
    )
    worksheet.append(
        [
            None,
            "Description",
            "Lease Commencement",
            "Rent Commencement",
            "Expiration",
            "Term",
            "Cite",
        ]
    )
    worksheet.append(
        [
            None,
            "Current Term",
            "01/03/2020",
            "01/03/2020",
            "02/28/2025",
            "60 Months",
            "3rd Amd., Pg 1",
        ]
    )
    worksheet.append(
        [
            None,
            "Rent Schedule:",
            "Rent Schedule:",
            "Rent Schedule:",
            "Rent Schedule:",
            "Rent Schedule:",
        ]
    )
    worksheet.append(
        [
            None,
            "Rent Type",
            "Begin Date",
            "End Date",
            "Monthly",
            "Annual",
            "SF",
            "PSF/Year",
            "Cite",
        ]
    )
    worksheet.append(
        [
            None,
            "Base rent",
            "01/03/2020",
            "02/28/2021",
            "$4,496.61",
            "$53,959.32",
            "4051",
            "$1.11",
            "3rd Amd., Pg 1",
        ]
    )
    worksheet.append([])
    worksheet.append(
        [
            None,
            "Expense Recoveries - CAM:",
            "Expense Recoveries - CAM:",
            "Expense Recoveries - CAM:",
            "Expense Recoveries - CAM:",
            "Expense Recoveries - CAM:",
            "Expense Recoveries - CAM:",
        ]
    )
    worksheet.append(
        [
            None,
            "Begin Date",
            "End Date",
            "Type",
            "Pro-rata share",
            "Base Year",
            "Cap",
        ]
    )
    worksheet.append(
        [
            None,
            "01/04/2024",
            "03/31/2027",
            "Net",
            "{{Pro Rata Share Financial_subsummarization}}",
            "{{Base Year_subsummarization}}",
            "{{CAP_subsummarization}}",
        ]
    )
    worksheet.append(
        [
            None,
            "Notes :",
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent.",
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent.",
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent.",
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent.",
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent.",
        ]
    )
    worksheet.append([])
    worksheet.append(
        [
            None,
            "Termination Options:",
            "Termination Options:",
            "Termination Options:",
            "Termination Options:",
            "Termination Options:",
        ]
    )
    worksheet.append(
        [
            None,
            "Description",
            "First Notice Date",
            "Last Notice Date",
            "Notice Period",
            "Fee",
        ]
    )
    worksheet.append([None, "Lease is Silent", None, None, None, None])
    worksheet.append(
        [
            None,
            "Termination Notes :",
            "{{Termination Notes_subsection no}}: {{Termination Notes_subsummarization}}",
            "{{Termination Notes_subsection no}}: {{Termination Notes_subsummarization}}",
            "{{Termination Notes_subsection no}}: {{Termination Notes_subsummarization}}",
            "{{Termination Notes_subsection no}}: {{Termination Notes_subsummarization}}",
        ]
    )
    worksheet.append(
        [
            None,
            "First Offer or Refusal :",
            "Art. 39: Lease is Silent",
            "Art. 39: Lease is Silent",
            "Art. 39: Lease is Silent",
            "Art. 39: Lease is Silent",
        ]
    )
    worksheet.append(
        [
            None,
            "Purchase :",
            "Art. 12: Lease is Silent",
            "Art. 12: Lease is Silent",
            "Art. 12: Lease is Silent",
            "Art. 12: Lease is Silent",
        ]
    )
    worksheet.append(
        [
            None,
            "Relocation :",
            "Lease is Silent",
            "Lease is Silent",
            "Lease is Silent",
            "Lease is Silent",
        ]
    )

    workbook.save(path)


async def main() -> None:
    workbook_path = UPLOADS_DIR / WORKBOOK_NAME
    create_workbook(workbook_path)
    validate_header_structure(HEADER_STRUCTURE)

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
    assert "field_locators" in fake_db.schemas.records[schema.id]
    assert fake_db.documents.records[document.id]["status"] == "extracted"
    assert fake_db.documents.records[document.id]["output_json"] == output_json
    assert fake_db.documents.records[document.id]["extraction_locators"]["version"] == 7
    assert (
        fake_db.documents.records[document.id]["extraction_locators"]["schema_id"]
        == schema.id
    )
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
    assert sheet_json["REBO Comment"] == {
        "row_2": "Lease ID reviewed from source document",
        "row_7": "Rent row needs legal review",
        "row_8": "CAM row confirmed",
    }
    assert output_json["Tenant Contact"]["LEASE/CONTACT INFORMATION:"] == {
        "Contact Name:": {
            "Billing Contact": None,
            "Notice Address": None,
            "REBO Comment": None,
        },
        "Phone Number:": {
            "Billing Contact": None,
            "Notice Address": "757-321-5000",
            "REBO Comment": "1. Updated Billing contact",
        },
        "Fax Number:": {
            "Billing Contact": None,
            "Notice Address": "757-321-5220",
            "REBO Comment": None,
        },
        "Email Address:": {
            "Billing Contact": "MS_Rents@dollartree.com",
            "Notice Address": None,
            "REBO Comment": None,
        },
        "Billing Name:": {
            "Billing Contact": "Dollar Tree Stores, Inc.",
            "Notice Address": "Dollar Tree Stores, Inc.",
            "REBO Comment": None,
        },
        "Attention:": {
            "Billing Contact": "Lease Accounting Dept 300",
            "Notice Address": "Lease Administration Department",
            "REBO Comment": None,
        },
        "Address:": {
            "Billing Contact": "500 Volvo Parkway",
            "Notice Address": "500 Volvo Parkway",
            "REBO Comment": None,
        },
        "City State Zip:": {
            "Billing Contact": "Chesapeake, VA 23320",
            "Notice Address": "Chesapeake, VA 23320",
            "REBO Comment": None,
        },
    }
    assert output_json["Annual Matrix"]["Annual Charges"] == {
        "CAM": {"2024": 100, "2025": 110, "2026": 120},
        "Tax": {"2024": 20, "2025": 25, "2026": 30},
    }

    pacific_schema = DocumentSchema(
        name="Pacific Clinics extraction layout",
        file_type="xlsx",
        fingerprint=build_header_structure_fingerprint(PACIFIC_HEADER_STRUCTURE),
        header_structure=PACIFIC_HEADER_STRUCTURE,
        field_locators={
            "version": 1,
            "type": "excel",
            "schema_id": "stale-schema-id",
            "sheets": [],
        },
    )
    source_pacific_path = UPLOADS_DIR / "step15-pacific-source.xlsx"
    matched_pacific_path = UPLOADS_DIR / "step15-pacific-matched.xlsx"
    create_pacific_workbook(
        source_pacific_path,
        lease_title="Lease Information: Uplift Family Services a California 501c3 at {{Property Name_subsummarization}}",
        tenant="Uplift Family Services a California 501c3",
        landlord="Grantor Real Estate Investments, LLC",
        zip_code=None,
    )
    create_pacific_workbook(
        matched_pacific_path,
        lease_title="Lease Information: Pacific Clinics at {{Property Name_subsummarization}}",
        tenant="Pacific Clinics",
        landlord="Hillandale Drive Properties, LLC",
        zip_code="90670",
    )

    source_document = Document(
        filename=source_pacific_path.name,
        file_type="xlsx",
        stored_path=f"uploads/{source_pacific_path.name}",
        status="approved",
        fingerprint=pacific_schema.fingerprint,
        matched_schema_id=pacific_schema.id,
    )
    matched_document = Document(
        filename=matched_pacific_path.name,
        file_type="xlsx",
        stored_path=f"uploads/{matched_pacific_path.name}",
        status="approved",
        fingerprint=pacific_schema.fingerprint,
        matched_schema_id=pacific_schema.id,
    )
    pacific_db = FakeDb([source_document, matched_document], pacific_schema)

    try:
        source_result = await extract_document_json(source_document.id, db=pacific_db)
        matched_result = await extract_document_json(matched_document.id, db=pacific_db)
    finally:
        source_pacific_path.unlink(missing_ok=True)
        matched_pacific_path.unlink(missing_ok=True)

    source_sheet = source_result["output_json"]["Lease_Abstract (1)"]
    matched_sheet = matched_result["output_json"]["Lease_Abstract (1)"]

    assert source_sheet["Lease Information"]["Tenant:"] == (
        "Uplift Family Services a California 501c3"
    )
    assert matched_sheet["Lease Information"]["Tenant:"] == "Pacific Clinics"
    assert matched_sheet["Lease Information"]["Landlord:"] == (
        "Hillandale Drive Properties, LLC"
    )
    assert "Lease_Abstract (1) section 1" not in matched_sheet
    assert matched_sheet["Property Information:"]["Address 2 :"] is None
    assert matched_sheet["Property Information:"]["Zip :"] == "90670"
    # Single placeholder, separator-joined placeholders, and multiline
    # placeholders all normalize to null; mixed content is preserved.
    assert matched_sheet["Property Information:"]["Property Name :"] is None
    assert matched_sheet["Property Information:"]["Suite Store :"] is None
    assert matched_sheet["Property Information:"]["Country :"] is None
    assert matched_sheet["Property Information:"]["Currency :"] == (
        "Article 12 - {{Currency Notes_subsummarization}}"
    )
    assert matched_sheet["Term Information:"] == [
        {
            "Description": "Current Term",
            "Lease Commencement": "01/03/2020",
            "Rent Commencement": "01/03/2020",
            "Expiration": "02/28/2025",
            "Term": "60 Months",
            "Cite": "3rd Amd., Pg 1",
        }
    ]
    assert matched_sheet["Rent Schedule:"] == [
        {
            "Rent Type": "Base rent",
            "Begin Date": "01/03/2020",
            "End Date": "02/28/2021",
            "Monthly": "$4,496.61",
            "Annual": "$53,959.32",
            "SF": "4051",
            "PSF/Year": "$1.11",
            "Cite": "3rd Amd., Pg 1",
        }
    ]
    assert matched_sheet["Expense Recoveries - CAM:"] == {
        "rows": [
            {
                "Begin Date": "01/04/2024",
                "End Date": "03/31/2027",
                "Type": "Net",
                "Pro-rata share": None,
                "Base Year": None,
                "Cap": None,
            }
        ],
        "Notes :": (
            "Add.,Art.51&56: TT shall pay operating expenses as additional rent."
        ),
    }
    assert "Lease_Abstract (1) section 9" not in matched_sheet
    assert matched_sheet["Termination Options:"] == {
        "rows": [
            {
                "Description": "Lease is Silent",
                "First Notice Date": None,
                "Last Notice Date": None,
                "Notice Period": None,
                "Fee": None,
            }
        ],
        "Termination Notes :": None,
        "First Offer or Refusal :": "Art. 39: Lease is Silent",
        "Purchase :": "Art. 12: Lease is Silent",
        "Relocation :": "Lease is Silent",
    }
    assert "Lease_Abstract (1) section 19" not in matched_sheet
    assert source_result["locators_created"] is True
    assert matched_result["locators_created"] is True
    assert (
        pacific_db.documents.records[source_document.id]["extraction_locators"]
        != pacific_db.documents.records[matched_document.id]["extraction_locators"]
    )

    print("Excel extraction check passed.")
    print("Charge Schedules rows:", len(sheet_json["Charge Schedules"]))
    print("Locators created first run:", first_result["locators_created"])
    print("Locators reused second run:", not second_result["locators_created"])
    print("Pacific tenant:", matched_sheet["Lease Information"]["Tenant:"])
    print("Pacific rent schedule rows:", len(matched_sheet["Rent Schedule:"]))


if __name__ == "__main__":
    asyncio.run(main())
