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
        confidence = 0.65 if flags else 0.8

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

    # ponytail: repeated/nested deterministic headers would otherwise emit
    # one flag per occurrence. Track what we've already flagged.
    emitted_missing_tables: set[tuple[str, str]] = set()
    emitted_missing_columns: set[tuple[str, str, str]] = set()

    for sheet_name, detected_tables in deterministic_headers.items():
        llm_tables = llm_tables_by_sheet.get(sheet_name, [])
        llm_tables_by_title = {
            _normalize_label(table["title"]): table
            for table in llm_tables
            if table.get("title")
        }

        for detected_table in detected_tables:
            title = detected_table.get("title")
            normalized_title = _normalize_label(title or "")

            if not normalized_title or normalized_title not in llm_tables_by_title:
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

            llm_header_names = _collect_header_names(
                llm_tables_by_title[normalized_title].get("headers", [])
            )
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

    deterministic_titles_by_sheet = {
        sheet_name: {
            _normalize_label(header["title"] or "")
            for header in headers
            if header.get("title")
        }
        for sheet_name, headers in deterministic_headers.items()
    }

    for sheet_name, llm_tables in llm_tables_by_sheet.items():
        deterministic_titles = deterministic_titles_by_sheet.get(sheet_name, set())
        for table in llm_tables:
            title = table.get("title")
            normalized_title = _normalize_label(title or "")
            if normalized_title and normalized_title in deterministic_titles:
                continue

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
