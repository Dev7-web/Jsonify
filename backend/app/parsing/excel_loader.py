import re
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


Grid = list[list[Any | None]]

_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_cell_value(value: Any) -> Any | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = _WHITESPACE_PATTERN.sub(" ", value).strip()
        return normalized or None

    return value


# Switch to streaming header detection if uploads grow much larger.
def load_sheets(path: str | Path) -> dict[str, Grid]:
    workbook_path = Path(path)

    if not workbook_path.is_file():
        raise FileNotFoundError(f"Excel file not found: {workbook_path}")

    try:
        workbook = load_workbook(workbook_path, data_only=True, read_only=True)
    except (BadZipFile, InvalidFileException) as error:
        raise ValueError(f"File is not a valid .xlsx workbook: {workbook_path}") from error

    try:
        sheets: dict[str, Grid] = {}

        for worksheet in workbook.worksheets:
            grid: Grid = []

            for row in worksheet.iter_rows(values_only=True):
                grid.append([normalize_cell_value(cell) for cell in row])

            sheets[worksheet.title] = grid

        return sheets
    finally:
        workbook.close()
