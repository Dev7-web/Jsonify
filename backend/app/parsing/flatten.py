from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any, TypedDict

from .excel_loader import _open_workbook, normalize_cell_value


class FlattenedSheet(TypedDict):
    name: str
    text: str


def flatten_excel(path: str | Path) -> str:
    """Return a compact, coordinate-preserving text rendering of an Excel file."""
    sections = flatten_excel_sections(path)
    return "\n\n".join(section["text"] for section in sections)


# ponytail: LLM-facing view keeps merged cells collapsed (one entry per merge)
# to save tokens. The header detector path (excel_loader.load_sheets) expands
# merges into the grid. Same file, two intentional views.
def flatten_excel_sections(path: str | Path) -> list[FlattenedSheet]:
    """Return one flattened text section per workbook sheet."""
    workbook = _open_workbook(path, read_only=True)

    try:
        sections: list[FlattenedSheet] = []

        for worksheet in workbook.worksheets:
            lines = [f"## Sheet: {worksheet.title}"]

            for row in worksheet.iter_rows():
                for cell in row:
                    value = normalize_cell_value(cell.value)
                    if value is None:
                        continue

                    lines.append(f"{cell.coordinate}={_format_cell_value(value)}")

            sections.append({"name": worksheet.title, "text": "\n".join(lines)})

        return sections
    finally:
        workbook.close()


def _format_cell_value(value: Any) -> str:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    return str(value)
