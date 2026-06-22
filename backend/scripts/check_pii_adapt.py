from __future__ import annotations

from pathlib import Path
import sys

from openpyxl import Workbook


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.pii.adapt import (
    cell_map_to_llm_text,
    contains_placeholder,
    excel_path_to_cell_map,
    sheets_to_cell_map,
)


def main() -> None:
    sheets = {
        "Sheet1": [
            ["Name", "Neha Rao"],
            ["Email", "neha@acme.in"],
            [None, ""],
        ],
        "Second": [["Phone", "+91-9000011111"]],
    }

    cell_map = sheets_to_cell_map(sheets)
    assert cell_map == {
        "Sheet1!A1": "Name",
        "Sheet1!B1": "Neha Rao",
        "Sheet1!A2": "Email",
        "Sheet1!B2": "neha@acme.in",
        "Second!A1": "Phone",
        "Second!B1": "+91-9000011111",
    }

    assert cell_map_to_llm_text(cell_map) == (
        "## Sheet: Sheet1\n"
        "A1=Name\n"
        "B1=Neha Rao\n"
        "A2=Email\n"
        "B2=neha@acme.in\n\n"
        "## Sheet: Second\n"
        "A1=Phone\n"
        "B1=+91-9000011111"
    )
    assert contains_placeholder("hello <PERSON_a1b2>") is True
    assert contains_placeholder("hello \\u003cPERSON_a1b2\\u003e") is True
    assert contains_placeholder("hello &lt;PERSON_a1b2&gt;") is True
    assert contains_placeholder("plain text") is False

    workbook_path = Path(__file__).resolve().parents[1] / "uploads" / "pii-adapt.xlsx"
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Compact"
    worksheet.append(["Title", "Title"])
    worksheet.append(["Name", "Neha Rao"])
    workbook.save(workbook_path)

    try:
        assert excel_path_to_cell_map(workbook_path) == {
            "Compact!A1": "Title",
            "Compact!B1": "Title",
            "Compact!A2": "Name",
            "Compact!B2": "Neha Rao",
        }
    finally:
        workbook_path.unlink(missing_ok=True)

    print("PII adapter check passed.")


if __name__ == "__main__":
    main()
