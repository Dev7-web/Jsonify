from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, TypedDict

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import BACKEND_ROOT
from app.db import get_db
from app.llm.header_prompt import HeaderStructure, adetect_header_structure
from app.models import Document
from app.parsing.excel_loader import load_sheets
from app.parsing.flatten import flatten_excel
from app.parsing.header_detector import DetectedHeader, detect_table_headers


DetectionSource = Literal["llm"]
DetectionFlagType = Literal[
    "deterministic_table_missing",
    "deterministic_column_missing",
    "llm_table_not_in_prescan",
]

_RESOLVED_BACKEND_ROOT = BACKEND_ROOT.resolve()


class DetectionFlag(TypedDict, total=False):
    type: DetectionFlagType
    sheet: str
    title: str | None
    column: str
    message: str


class DetectionResult(TypedDict):
    source: DetectionSource
    header_structure: HeaderStructure
    deterministic_headers: dict[str, list[DetectedHeader]]
    flags: list[DetectionFlag]
    confidence: float


class DocumentDetectionResponse(TypedDict):
    id: str
    status: str
    confidence: float
    detected_headers: DetectionResult


class DetectHeadersError(RuntimeError):
    pass


class DocumentNotFoundError(DetectHeadersError):
    pass


class UnsupportedDocumentTypeError(DetectHeadersError):
    pass


class StoredDocumentFileError(DetectHeadersError):
    pass


class InvalidDocumentFileError(DetectHeadersError):
    pass


class DetectionInProgressError(DetectHeadersError):
    pass


async def detect_document_headers(
    document_id: str,
    *,
    db: AsyncIOMotorDatabase | None = None,
) -> DocumentDetectionResponse:
    database = db if db is not None else get_db()
    record = await database.documents.find_one({"_id": document_id})
    if record is None:
        raise DocumentNotFoundError(f"Document not found: {document_id}")

    # Validate the persisted record before touching status. Any failure here
    # (broken record, wrong file type) marks the document as failed.
    try:
        document = Document.model_validate(record)
        _ensure_xlsx_document(document)
    except Exception as error:
        await _mark_document_failed(database, document_id, reason=str(error))
        raise

    # Atomic status transition: the conditional `$ne: "detecting"` makes this
    # the lock. A second concurrent caller sees matched_count == 0 and bails
    # out with DetectionInProgressError — no duplicate LLM call.
    transition = await database.documents.update_one(
        {"_id": document_id, "status": {"$ne": "detecting"}},
        {
            "$set": {"status": "detecting"},
            "$unset": {
                "detected_headers": "",
                "confidence": "",
                "failure_reason": "",
            },
        },
    )
    if transition.matched_count == 0:
        raise DetectionInProgressError(
            f"Detection is already running for document {document_id}."
        )

    try:
        workbook_path = _resolve_stored_path(document.stored_path)
        deterministic_headers = _detect_deterministic_headers(workbook_path)
        flattened_text = _flatten_workbook(workbook_path)
        header_structure = await adetect_header_structure(flattened_text)

        flags = compare_with_deterministic_headers(
            header_structure,
            deterministic_headers,
        )
        confidence = calculate_detection_confidence(
            flags,
            header_structure,
            deterministic_headers,
        )

        detection_result: DetectionResult = {
            "source": "llm",
            "header_structure": header_structure,
            "deterministic_headers": deterministic_headers,
            "flags": flags,
            "confidence": confidence,
        }

        await database.documents.update_one(
            {"_id": document_id},
            {
                "$set": {
                    "status": "needs_review",
                    "detected_headers": detection_result,
                    "confidence": confidence,
                }
            },
        )

        return {
            "id": document_id,
            "status": "needs_review",
            "confidence": confidence,
            "detected_headers": detection_result,
        }
    except Exception as error:
        await _mark_document_failed(database, document_id, reason=str(error))
        raise


def compare_with_deterministic_headers(
    header_structure: HeaderStructure,
    deterministic_headers: dict[str, list[DetectedHeader]],
) -> list[DetectionFlag]:
    flags: list[DetectionFlag] = []
    llm_tables_by_sheet = _get_llm_table_sections_by_sheet(header_structure)
    matched_llm_table_keys: set[tuple[str, str]] = set()

    # ponytail: repeated/nested deterministic headers would otherwise emit
    # one flag per occurrence. Track what we've already flagged.
    emitted_missing_tables: set[tuple[str, str]] = set()
    emitted_missing_columns: set[tuple[str, str, str]] = set()
    emitted_unmatched_llm_tables: set[tuple[str, str]] = set()

    for sheet_name, detected_tables in deterministic_headers.items():
        llm_tables = llm_tables_by_sheet.get(sheet_name, [])

        for detected_table in detected_tables:
            title = detected_table.get("title")
            normalized_title = _normalize_label(title or "")
            matched_llm_table = _find_matching_llm_table(detected_table, llm_tables)

            if matched_llm_table is None:
                key = (sheet_name, normalized_title)
                if key in emitted_missing_tables:
                    continue
                emitted_missing_tables.add(key)

                flags.append(
                    {
                        "type": "deterministic_table_missing",
                        "sheet": sheet_name,
                        "title": title,
                        "message": _format_missing_table_message(sheet_name, title),
                    }
                )
                continue

            matched_llm_table_keys.add((sheet_name, _get_llm_table_key(matched_llm_table)))

            llm_header_names = _collect_header_names(matched_llm_table.get("headers", []))
            for column in detected_table["columns"]:
                normalized_column = _normalize_label(column["name"])
                if normalized_column in llm_header_names:
                    continue

                column_key = (sheet_name, normalized_title, normalized_column)
                if column_key in emitted_missing_columns:
                    continue
                emitted_missing_columns.add(column_key)

                flags.append(
                    {
                        "type": "deterministic_column_missing",
                        "sheet": sheet_name,
                        "title": title,
                        "column": column["name"],
                        "message": (
                            f'Deterministic column "{column["name"]}" from '
                            f'"{title or "Untitled table"}" was not returned by the LLM.'
                        ),
                    }
                )

    for sheet_name, llm_tables in llm_tables_by_sheet.items():
        for table in llm_tables:
            if (sheet_name, _get_llm_table_key(table)) in matched_llm_table_keys:
                continue

            unmatched_key = (sheet_name, _normalize_label(table.get("title") or ""))
            if unmatched_key in emitted_unmatched_llm_tables:
                continue
            emitted_unmatched_llm_tables.add(unmatched_key)

            flags.append(
                {
                    "type": "llm_table_not_in_prescan",
                    "sheet": sheet_name,
                    "title": title,
                    "message": _format_llm_table_not_in_prescan_message(
                        sheet_name,
                        title,
                    ),
                }
            )

    return flags


def calculate_detection_confidence(
    flags: list[DetectionFlag],
    header_structure: HeaderStructure,
    deterministic_headers: dict[str, list[DetectedHeader]],
) -> float:
    if not flags:
        return 0.8

    deterministic_table_count = sum(len(tables) for tables in deterministic_headers.values())
    deterministic_column_count = sum(
        len(table["columns"])
        for tables in deterministic_headers.values()
        for table in tables
    )
    llm_table_count = sum(
        1
        for sheet in header_structure["sheets"]
        for section in sheet["sections"]
        if section.get("type") == "table"
    )

    flag_counts = _count_flags_by_type(flags)
    table_missing_ratio = _ratio(
        flag_counts.get("deterministic_table_missing", 0),
        deterministic_table_count,
    )
    column_missing_ratio = _ratio(
        flag_counts.get("deterministic_column_missing", 0),
        deterministic_column_count,
    )
    llm_unmatched_ratio = _ratio(
        flag_counts.get("llm_table_not_in_prescan", 0),
        llm_table_count,
    )

    penalty = (
        min(0.16, table_missing_ratio * 0.16)
        + min(0.12, column_missing_ratio * 0.12)
        + min(0.08, llm_unmatched_ratio * 0.08)
    )
    return round(max(0.5, 0.8 - penalty), 2)


def _ensure_xlsx_document(document: Document) -> None:
    if document.file_type != "xlsx":
        raise UnsupportedDocumentTypeError("Only .xlsx header detection is supported.")


def _resolve_stored_path(stored_path: str) -> Path:
    relative_path = Path(stored_path)
    if relative_path.is_absolute():
        raise StoredDocumentFileError("Stored document path must be relative.")

    resolved_path = (_RESOLVED_BACKEND_ROOT / relative_path).resolve()

    try:
        resolved_path.relative_to(_RESOLVED_BACKEND_ROOT)
    except ValueError as error:
        raise StoredDocumentFileError("Stored document path escapes backend root.") from error

    if not resolved_path.is_file():
        raise StoredDocumentFileError(f"Stored document file not found: {stored_path}")

    return resolved_path


def _detect_deterministic_headers(path: Path) -> dict[str, list[DetectedHeader]]:
    try:
        return {
            sheet_name: detect_table_headers(grid)
            for sheet_name, grid in load_sheets(path).items()
        }
    except FileNotFoundError as error:
        raise StoredDocumentFileError(str(error)) from error
    except ValueError as error:
        raise InvalidDocumentFileError(str(error)) from error


def _flatten_workbook(path: Path) -> str:
    try:
        return flatten_excel(path)
    except FileNotFoundError as error:
        raise StoredDocumentFileError(str(error)) from error
    except ValueError as error:
        raise InvalidDocumentFileError(str(error)) from error


def _get_llm_table_sections_by_sheet(
    header_structure: HeaderStructure,
) -> dict[str, list[dict[str, Any]]]:
    tables_by_sheet: dict[str, list[dict[str, Any]]] = {}

    for sheet in header_structure["sheets"]:
        tables = [
            dict(section)
            for section in sheet["sections"]
            if section.get("type") == "table"
        ]
        tables_by_sheet[sheet["name"]] = tables

    return tables_by_sheet


def _find_matching_llm_table(
    detected_table: DetectedHeader,
    llm_tables: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_title = _normalize_label(detected_table.get("title") or "")
    if normalized_title:
        for table in llm_tables:
            if _normalize_label(table.get("title") or "") == normalized_title:
                return table

    detected_column_names = {
        _normalize_label(column["name"])
        for column in detected_table["columns"]
        if _normalize_label(column["name"])
    }
    if not detected_column_names:
        return None

    best_table: dict[str, Any] | None = None
    best_score = 0.0

    for table in llm_tables:
        llm_header_names = _collect_header_names(table.get("headers", []))
        if not llm_header_names:
            continue

        overlap_count = len(detected_column_names & llm_header_names)
        if overlap_count < 2:
            continue

        score = overlap_count / min(len(detected_column_names), len(llm_header_names))
        if score > best_score:
            best_table = table
            best_score = score

    if best_score < 0.5:
        return None

    return best_table


def _get_llm_table_key(table: dict[str, Any]) -> str:
    title = _normalize_label(table.get("title") or "")
    header_names = sorted(_collect_header_names(table.get("headers", [])))
    return f"{title}|{'|'.join(header_names)}"


def _collect_header_names(headers: Any) -> set[str]:
    names: set[str] = set()

    if not isinstance(headers, list):
        return names

    for header in headers:
        if not isinstance(header, dict):
            continue

        name = header.get("name")
        if isinstance(name, str):
            names.add(_normalize_label(name))

        names.update(_collect_header_names(header.get("subheaders", [])))

    return names


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _count_flags_by_type(flags: list[DetectionFlag]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for flag in flags:
        flag_type = flag["type"]
        counts[flag_type] = counts.get(flag_type, 0) + 1
    return counts


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0

    return min(1.0, numerator / denominator)


def _format_missing_table_message(sheet_name: str, title: str | None) -> str:
    table_title = title or "Untitled table"
    return (
        f'Deterministic table "{table_title}" on sheet "{sheet_name}" '
        "was not returned by the LLM."
    )


def _format_llm_table_not_in_prescan_message(sheet_name: str, title: str | None) -> str:
    table_title = title or "Untitled table"
    return (
        f'LLM table "{table_title}" on sheet "{sheet_name}" '
        "was not found by deterministic pre-scan."
    )


async def _mark_document_failed(
    db: AsyncIOMotorDatabase,
    document_id: str,
    *,
    reason: str,
) -> None:
    await db.documents.update_one(
        {"_id": document_id},
        {
            "$set": {"status": "failed", "failure_reason": reason},
            "$unset": {"confidence": ""},
        },
    )
