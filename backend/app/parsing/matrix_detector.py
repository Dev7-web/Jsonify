from __future__ import annotations

import re
from typing import Any, TypedDict

from openpyxl.utils import get_column_letter

from .excel_loader import Grid


class MatrixColumn(TypedDict):
    name: str
    start_column_index: int
    end_column_index: int
    coordinate: str


class DetectedMatrix(TypedDict, total=False):
    title: str | None
    title_coordinate: str | None
    header_row_index: int
    row_label_column_index: int
    data_start_row_index: int
    data_end_row_index: int
    headers: list[MatrixColumn]


_MIN_MATRIX_HEADERS = 2
_MIN_MATRIX_ROW_LABELS = 2
_MIN_POPULATED_MATRIX_ROWS = 2
_MAX_MATRIX_SCAN_ROWS = 80
_TITLE_LOOKBACK_ROWS = 3
_BLANK_ROW_STOP_COUNT = 2
_MAX_MATRIX_HEADER_LENGTH = 60
_MAX_MATRIX_DATA_START_GAP = 2
_YEAR_HEADER_PATTERN = re.compile(r"^(?:19|20|21)\d{2}$")
_ORDINAL_PERIOD_HEADER_PATTERN = re.compile(
    r"^\d+(?:st|nd|rd|th)\s+(?:year|month|quarter)$",
    re.IGNORECASE,
)


def detect_matrices(
    grid: Grid,
    *,
    start_row: int = 1,
    end_row: int | None = None,
) -> list[DetectedMatrix]:
    """Detect row-label-by-column-header comparison grids.

    A matrix requires a blank top-left intersection, at least two horizontal
    headers, a separate repeated row-label column to their left, and at least
    two rows with values under the matrix headers.
    """
    if not grid:
        return []

    bounded_start = max(1, start_row)
    bounded_end = min(end_row or len(grid), len(grid))
    detected: list[DetectedMatrix] = []
    occupied_ranges: list[tuple[int, int]] = []

    for header_row_index in range(bounded_start, bounded_end + 1):
        header_runs = _header_runs(
            grid[header_row_index - 1],
            row_index=header_row_index,
        )
        if len(header_runs) < _MIN_MATRIX_HEADERS:
            continue

        candidate = _build_matrix_candidate(
            grid,
            header_row_index=header_row_index,
            header_runs=header_runs,
            end_row=bounded_end,
        )
        if candidate is None:
            continue

        candidate_range = (
            candidate["header_row_index"],
            candidate["data_end_row_index"],
        )
        if any(_ranges_overlap(candidate_range, occupied) for occupied in occupied_ranges):
            continue

        occupied_ranges.append(candidate_range)
        detected.append(candidate)

    return detected


def _build_matrix_candidate(
    grid: Grid,
    *,
    header_row_index: int,
    header_runs: list[MatrixColumn],
    end_row: int,
) -> DetectedMatrix | None:
    first_header_column = min(
        header["start_column_index"] for header in header_runs
    )
    if first_header_column <= 1:
        return None

    row_label_score = _find_row_label_column(
        grid,
        header_row_index=header_row_index,
        headers=header_runs,
        first_header_column=first_header_column,
        end_row=end_row,
    )
    if row_label_score is None:
        return None

    row_label_column_index, label_rows, populated_rows = row_label_score
    if len(label_rows) < _MIN_MATRIX_ROW_LABELS:
        return None
    if len(populated_rows) < _MIN_POPULATED_MATRIX_ROWS:
        return None

    data_start_row_index = min(label_rows)
    if data_start_row_index > header_row_index + _MAX_MATRIX_DATA_START_GAP:
        return None

    data_end_row_index = _find_matrix_data_end_row(
        grid,
        row_label_column_index=row_label_column_index,
        headers=header_runs,
        start_row=data_start_row_index,
        end_row=end_row,
    )
    title, title_coordinate = _find_matrix_title(
        grid,
        header_row_index=header_row_index,
        first_header_column=first_header_column,
    )

    return {
        "title": title,
        "title_coordinate": title_coordinate,
        "header_row_index": header_row_index,
        "row_label_column_index": row_label_column_index,
        "data_start_row_index": data_start_row_index,
        "data_end_row_index": data_end_row_index,
        "headers": header_runs,
    }


def _header_runs(
    row: list[Any | None],
    *,
    row_index: int,
) -> list[MatrixColumn]:
    runs: list[MatrixColumn] = []
    seen_headers: set[str] = set()
    column_offset = 0

    while column_offset < len(row):
        value = row[column_offset]
        name = _display_text(value)
        if not name:
            column_offset += 1
            continue

        normalized = _normalize(value)
        start_column_index = column_offset + 1
        end_offset = column_offset
        while (
            end_offset + 1 < len(row)
            and _normalize(row[end_offset + 1]) == normalized
        ):
            end_offset += 1

        if _is_probable_matrix_header(name) and normalized not in seen_headers:
            seen_headers.add(normalized)
            runs.append(
                {
                    "name": name,
                    "start_column_index": start_column_index,
                    "end_column_index": end_offset + 1,
                    "coordinate": _coordinate(
                        row_index=row_index,
                        column_index=start_column_index,
                    ),
                }
            )

        column_offset = end_offset + 1

    return runs


def _find_row_label_column(
    grid: Grid,
    *,
    header_row_index: int,
    headers: list[MatrixColumn],
    first_header_column: int,
    end_row: int,
) -> tuple[int, list[int], list[int]] | None:
    best_score: tuple[int, int, int, int] | None = None
    best_column_index: int | None = None
    best_label_rows: list[int] = []
    best_populated_rows: list[int] = []
    scan_end = min(end_row, header_row_index + _MAX_MATRIX_SCAN_ROWS)

    for column_index in range(1, first_header_column):
        if not _is_empty(_cell_at(grid, header_row_index, column_index)):
            continue

        label_rows: list[int] = []
        populated_rows: list[int] = []
        colon_label_count = 0

        for row_index in range(header_row_index + 1, scan_end + 1):
            label = _cell_at(grid, row_index, column_index)
            if not _is_probable_row_label(label):
                continue
            if _row_label_repeats_in_matrix_values(
                grid[row_index - 1],
                label=label,
                headers=headers,
            ):
                continue

            label_rows.append(row_index)
            if str(label).strip().endswith(":"):
                colon_label_count += 1
            if _row_has_matrix_value(grid[row_index - 1], headers):
                populated_rows.append(row_index)

        if len(label_rows) < _MIN_MATRIX_ROW_LABELS:
            continue
        if len(populated_rows) < _MIN_POPULATED_MATRIX_ROWS:
            continue

        score = (
            len(populated_rows),
            colon_label_count,
            len(label_rows),
            -column_index,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_column_index = column_index
            best_label_rows = label_rows
            best_populated_rows = populated_rows

    if best_column_index is None:
        return None

    return best_column_index, best_label_rows, best_populated_rows


def _find_matrix_data_end_row(
    grid: Grid,
    *,
    row_label_column_index: int,
    headers: list[MatrixColumn],
    start_row: int,
    end_row: int,
) -> int:
    last_label_row = start_row
    blank_row_count = 0

    for row_index in range(start_row, min(end_row, len(grid)) + 1):
        label = _cell_at(grid, row_index, row_label_column_index)
        has_label = _is_probable_row_label(label)
        has_value = _row_has_matrix_value(grid[row_index - 1], headers)

        if has_label:
            last_label_row = row_index
            blank_row_count = 0
            continue

        if has_value:
            blank_row_count = 0
            continue

        blank_row_count += 1
        if blank_row_count >= _BLANK_ROW_STOP_COUNT:
            break

    return last_label_row


def _find_matrix_title(
    grid: Grid,
    *,
    header_row_index: int,
    first_header_column: int,
) -> tuple[str | None, str | None]:
    start_row = max(1, header_row_index - _TITLE_LOOKBACK_ROWS)

    for row_index in range(header_row_index - 1, start_row - 1, -1):
        unique_values: list[tuple[int, str]] = []
        seen: set[str] = set()

        for column_index, value in enumerate(grid[row_index - 1], start=1):
            text = _display_text(value)
            normalized = _normalize(value)
            if not text or not normalized or normalized in seen:
                continue
            if column_index > first_header_column:
                continue

            seen.add(normalized)
            unique_values.append((column_index, text))

        if len(unique_values) != 1:
            continue

        column_index, title = unique_values[0]
        return title, _coordinate(row_index, column_index)

    return None, None


def _row_has_matrix_value(
    row: list[Any | None],
    headers: list[MatrixColumn],
) -> bool:
    return any(
        not _is_empty(_cell_at_row(row, column_index))
        for header in headers
        for column_index in range(
            header["start_column_index"],
            header["end_column_index"] + 1,
        )
    )


def _row_label_repeats_in_matrix_values(
    row: list[Any | None],
    *,
    label: Any,
    headers: list[MatrixColumn],
) -> bool:
    normalized_label = _normalize(label)
    if not normalized_label:
        return False

    return any(
        _normalize(_cell_at_row(row, column_index)) == normalized_label
        for header in headers
        for column_index in range(
            header["start_column_index"],
            header["end_column_index"] + 1,
        )
    )


def _is_probable_row_label(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False

    text = _display_text(value)
    if not text or len(text) > 100:
        return False

    return any(character.isalpha() for character in text)


def _is_probable_matrix_header(value: str) -> bool:
    if len(value) > _MAX_MATRIX_HEADER_LENGTH:
        return False

    if _YEAR_HEADER_PATTERN.fullmatch(value):
        return True
    if _ORDINAL_PERIOD_HEADER_PATTERN.fullmatch(value):
        return True
    if value[0].isdigit():
        return False

    return any(character.isalpha() for character in value)


def _cell_at(grid: Grid, row_index: int, column_index: int) -> Any | None:
    if row_index < 1 or row_index > len(grid):
        return None

    return _cell_at_row(grid[row_index - 1], column_index)


def _cell_at_row(row: list[Any | None], column_index: int) -> Any | None:
    column_offset = column_index - 1
    if column_offset < 0 or column_offset >= len(row):
        return None

    return row[column_offset]


def _display_text(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def _normalize(value: Any) -> str:
    return _display_text(value).casefold()


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coordinate(row_index: int, column_index: int) -> str:
    return f"{get_column_letter(column_index)}{row_index}"


def _ranges_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]
