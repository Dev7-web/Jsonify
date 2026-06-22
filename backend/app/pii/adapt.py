from __future__ import annotations

from collections import OrderedDict
import re
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter

from app.parsing.flatten import flatten_excel_sections


PLACEHOLDER_RE = re.compile(r"<[A-Z_]+_[A-Fa-f0-9]{4,}>")
JSON_ESCAPED_PLACEHOLDER_RE = re.compile(
    r"\\u003[cC][A-Z_]+_[A-Fa-f0-9]{4,}\\u003[eE]"
)
HTML_ESCAPED_PLACEHOLDER_RE = re.compile(r"&lt;[A-Z_]+_[A-Fa-f0-9]{4,}&gt;")


def excel_path_to_cell_map(path: str | Path) -> dict[str, str]:
    cell_map: dict[str, str] = OrderedDict()
    for section in flatten_excel_sections(path):
        sheet_name = section["name"]
        for line in section["text"].splitlines():
            if line.startswith("## Sheet:") or "=" not in line:
                continue

            coordinate, value = line.split("=", 1)
            if value == "":
                continue

            cell_map[f"{sheet_name}!{coordinate}"] = value

    return cell_map


def sheets_to_cell_map(sheets: dict[str, list[list[Any | None]]]) -> dict[str, str]:
    cell_map: dict[str, str] = OrderedDict()
    for sheet_name, grid in sheets.items():
        for row_index, row in enumerate(grid, start=1):
            for column_index, value in enumerate(row, start=1):
                if value is None:
                    continue

                text = str(value)
                if text == "":
                    continue

                coordinate = f"{get_column_letter(column_index)}{row_index}"
                cell_map[f"{sheet_name}!{coordinate}"] = text

    return cell_map


def cell_map_to_llm_text(cell_map: dict[str, str]) -> str:
    sections: OrderedDict[str, list[str]] = OrderedDict()
    for locator, value in cell_map.items():
        sheet_name, coordinate = _split_locator(locator)
        if sheet_name not in sections:
            sections[sheet_name] = [f"## Sheet: {sheet_name}"]

        sections[sheet_name].append(f"{coordinate}={value}")

    return "\n\n".join("\n".join(lines) for lines in sections.values())


def contains_placeholder(text: str) -> bool:
    return (
        PLACEHOLDER_RE.search(text) is not None
        or JSON_ESCAPED_PLACEHOLDER_RE.search(text) is not None
        or HTML_ESCAPED_PLACEHOLDER_RE.search(text) is not None
    )


def normalize_placeholder_tokens(text: str) -> str:
    return (
        text.replace("\\u003c", "<")
        .replace("\\u003C", "<")
        .replace("\\u003e", ">")
        .replace("\\u003E", ">")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _split_locator(locator: str) -> tuple[str, str]:
    if "!" not in locator:
        return "", locator

    return locator.rsplit("!", 1)
