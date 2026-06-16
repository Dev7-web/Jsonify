from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, TypedDict

from openpyxl.utils import get_column_letter

from .excel_loader import Grid


class HeaderColumn(TypedDict):
    name: str
    column_index: int
    coordinate: str


class DetectedHeader(TypedDict):
    title: str | None
    title_coordinate: str | None
    row_index: int
    coordinate: str
    columns: list[HeaderColumn]


# return multiple entries by design — dedup is the caller's job.
MIN_HEADER_STRING_CELLS = 3
MIN_HEADER_STRING_RATIO = 0.75
LOOKAHEAD_ROWS = 3
TITLE_LOOKBACK_ROWS = 3


def detect_table_headers(
    grid: Grid,
    *,
    min_string_cells: int = MIN_HEADER_STRING_CELLS,
    min_string_ratio: float = MIN_HEADER_STRING_RATIO,
    lookahead_rows: int = LOOKAHEAD_ROWS,
    title_lookback_rows: int = TITLE_LOOKBACK_ROWS,
) -> list[DetectedHeader]:
    """Return every row in `grid` that looks like a table header.

    Repeated header rows (e.g., page-break repeats, nested sub-headers) all
    appear in the result list; deduplicating is the caller's responsibility.
    """
    headers: list[DetectedHeader] = []

    for row_offset, row in enumerate(grid):
        row_index = row_offset + 1
        header_columns = _get_header_columns(
            row, row_index, min_string_cells, min_string_ratio
        )
        if not header_columns:
            continue

        if not _has_data_rows_under_header(
            grid, row_offset, header_columns, lookahead_rows
        ):
            continue

        title, title_coordinate = _find_table_title(
            grid, row_offset, title_lookback_rows
        )
        first_column_index = header_columns[0]["column_index"]

        headers.append(
            {
                "title": title,
                "title_coordinate": title_coordinate,
                "row_index": row_index,
                "coordinate": _cell_coordinate(row_index, first_column_index),
                "columns": header_columns,
            }
        )

    return headers


def _get_header_columns(
    row: list[Any | None],
    row_index: int,
    min_string_cells: int,
    min_string_ratio: float,
) -> list[HeaderColumn]:
    non_empty_cells = [
        (column_offset + 1, value)
        for column_offset, value in enumerate(row)
        if value is not None
    ]
    string_cells = [
        (column_index, value)
        for column_index, value in non_empty_cells
        if _is_text(value)
    ]

    if len(string_cells) < min_string_cells:
        return []

    string_ratio = len(string_cells) / len(non_empty_cells)
    if string_ratio < min_string_ratio:
        return []

    return [
        {
            "name": str(value),
            "column_index": column_index,
            "coordinate": _cell_coordinate(row_index, column_index),
        }
        for column_index, value in string_cells
    ]


def _has_data_rows_under_header(
    grid: Grid,
    header_row_offset: int,
    header_columns: list[HeaderColumn],
    lookahead_rows: int,
) -> bool:
    for row in grid[header_row_offset + 1 : header_row_offset + 1 + lookahead_rows]:
        row_values = []
        for column in header_columns:
            value = _cell_at(row, column["column_index"])
            if value is not None:
                row_values.append(value)

        if _is_data_row(row_values):
            return True

    return False


# ponytail: any row with >=2 non-empty cells beneath a candidate header counts
# as data. Loose on purpose — earlier numeric-majority rule missed text-only
# tables (contact lists, lease sheets). Tighten when false positives appear.
def _is_data_row(values: list[Any]) -> bool:
    return len(values) >= 2


def _find_table_title(
    grid: Grid,
    header_row_offset: int,
    title_lookback_rows: int,
) -> tuple[str | None, str | None]:
    start_offset = max(0, header_row_offset - title_lookback_rows)

    for row_offset in range(header_row_offset - 1, start_offset - 1, -1):
        title_cells = [
            (column_offset + 1, value)
            for column_offset, value in enumerate(grid[row_offset])
            if value is not None
        ]
        if len(title_cells) != 1:
            continue

        column_index, value = title_cells[0]
        if not _is_text(value):
            continue

        row_index = row_offset + 1
        return str(value), _cell_coordinate(row_index, column_index)

    return None, None


def _cell_at(row: list[Any | None], column_index: int) -> Any | None:
    column_offset = column_index - 1
    if column_offset >= len(row):
        return None

    return row[column_offset]


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_numeric_or_date(value: Any) -> bool:
    if isinstance(value, bool):
        return False

    return isinstance(value, (int, float, Decimal, date, datetime, time))


def _cell_coordinate(row_index: int, column_index: int) -> str:
    return f"{get_column_letter(column_index)}{row_index}"
