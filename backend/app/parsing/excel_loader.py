import re
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet


Grid = list[list[Any | None]]

_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_cell_value(value: Any) -> Any | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = _WHITESPACE_PATTERN.sub(" ", value).strip()
        return normalized or None

    return value


def _expand_merged_cells(worksheet: Worksheet) -> None:
    # Copy each merged range's top-left value into every cell in the range,
    # so downstream consumers (header detection) see merged headers like
    # `Q1 2024` spanning A1:D1 as four populated cells instead of one + Nones.
    for merged_range in list(worksheet.merged_cells.ranges):
        top_left_value = worksheet.cell(merged_range.min_row, merged_range.min_col).value
        worksheet.unmerge_cells(str(merged_range))
        for row_index in range(merged_range.min_row, merged_range.max_row + 1):
            for column_index in range(merged_range.min_col, merged_range.max_col + 1):
                worksheet.cell(row_index, column_index).value = top_left_value


# ponytail: full grid kept in memory (no read_only mode, since merged-cell info
# is only available in normal mode). Safe under the 25 MB upload limit.
# Switch to streaming header detection if uploads grow much larger.
def load_sheets(path: str | Path) -> dict[str, Grid]:
    workbook_path = Path(path)

    if not workbook_path.is_file():
        raise FileNotFoundError(f"Excel file not found: {workbook_path}")

    try:
        workbook = load_workbook(workbook_path, data_only=True)
    except (BadZipFile, InvalidFileException) as error:
        raise ValueError(f"File is not a valid .xlsx workbook: {workbook_path}") from error

    try:
        sheets: dict[str, Grid] = {}

        for worksheet in workbook.worksheets:
            _expand_merged_cells(worksheet)

            grid: Grid = []

            for row in worksheet.iter_rows(values_only=True):
                grid.append([normalize_cell_value(cell) for cell in row])

            sheets[worksheet.title] = grid

        return sheets
    finally:
        workbook.close()
