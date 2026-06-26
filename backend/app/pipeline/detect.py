from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import BACKEND_ROOT
from app.db import get_db
from app.llm.header_prompt import (
    HeaderStructure,
    adetect_header_structure_from_cell_map,
    validate_header_structure,
)
from app.models import Document
from app.parsing.excel_loader import load_sheets
from app.parsing.form_detector import DetectedKeyValueSection, detect_key_value_sections
from app.parsing.header_detector import DetectedHeader, detect_table_headers
from app.parsing.matrix_detector import DetectedMatrix, detect_matrices
from app.pii.adapt import (
    contains_placeholder,
    excel_path_to_cell_map,
    normalize_placeholder_tokens,
)
from app.pii.client import PiiServiceError, pii_restore
from app.pipeline.fingerprint import HeaderFingerprint, SchemaMatch
from app.pipeline.fingerprint import build_deterministic_header_fingerprint
from app.pipeline.fingerprint import build_deterministic_layout_fingerprint
from app.pipeline.fingerprint import build_header_label_fingerprint
from app.pipeline.fingerprint import match_schema_by_deterministic_fingerprint
from app.pipeline.fingerprint import match_schema_by_fingerprint


DetectionSource = Literal["llm", "schema"]
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


class DetectionResult(TypedDict, total=False):
    source: DetectionSource
    header_structure: HeaderStructure
    deterministic_headers: dict[str, list[DetectedHeader]]
    deterministic_key_value_sections: dict[str, list[DetectedKeyValueSection]]
    deterministic_matrices: dict[str, list[DetectedMatrix]]
    deterministic_fingerprint: str
    flags: list[DetectionFlag]
    confidence: float
    match: dict[str, Any]


class DocumentDetectionResponse(TypedDict, total=False):
    id: str
    status: str
    source: DetectionSource
    confidence: float
    detected_headers: DetectionResult
    fingerprint: str
    deterministic_fingerprint: str
    matched_schema_id: str


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
                "fingerprint": "",
                "matched_schema_id": "",
                "pii_document_id": "",
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
        deterministic_headers, deterministic_matrices = (
            _detect_deterministic_tabular_layout(workbook_path)
        )
        deterministic_key_value_sections = _detect_deterministic_key_value_sections(
            workbook_path,
            deterministic_matrices,
        )
        deterministic_fingerprint = _build_deterministic_layout_match_fingerprint(
            deterministic_headers,
            deterministic_key_value_sections,
            deterministic_matrices,
        )

        schema_response = await _detect_known_schema(
            database,
            document=document,
            deterministic_headers=deterministic_headers,
            deterministic_key_value_sections=deterministic_key_value_sections,
            deterministic_matrices=deterministic_matrices,
            deterministic_fingerprint=deterministic_fingerprint,
        )
        if schema_response is not None:
            return schema_response

        pii_document_ids: list[str] = []
        cell_map = _build_llm_cell_map(workbook_path)
        header_structure = await adetect_header_structure_from_cell_map(
            cell_map,
            pii_document_id_callback=pii_document_ids.append,
        )
        pii_document_id = pii_document_ids[-1] if pii_document_ids else None
        header_structure = await _restore_header_structure_placeholders(
            header_structure,
            pii_document_id,
        )
        header_structure = apply_key_value_section_hints(
            header_structure,
            deterministic_key_value_sections,
        )
        header_structure = apply_matrix_section_hints(
            header_structure,
            deterministic_matrices,
        )
        _raise_if_header_structure_has_placeholders(header_structure)

        flags = compare_with_deterministic_headers(
            header_structure,
            deterministic_headers,
        )
        confidence = calculate_detection_confidence(
            flags,
            header_structure,
            deterministic_headers,
        )
        try:
            candidate_fingerprint = build_header_label_fingerprint(header_structure)
        except ValueError:
            candidate_fingerprint = None

        detection_result: DetectionResult = {
            "source": "llm",
            "header_structure": header_structure,
            "deterministic_headers": deterministic_headers,
            "deterministic_key_value_sections": deterministic_key_value_sections,
            "deterministic_matrices": deterministic_matrices,
            "flags": flags,
            "confidence": confidence,
        }
        if deterministic_fingerprint is not None:
            detection_result[
                "deterministic_fingerprint"
            ] = deterministic_fingerprint.fingerprint

        update_fields: dict[str, Any] = {
            "status": "needs_review",
            "detected_headers": detection_result,
            "confidence": confidence,
        }
        if pii_document_ids:
            update_fields["pii_document_id"] = pii_document_ids[-1]
        unset_fields: dict[str, str] = {}
        if candidate_fingerprint is not None:
            update_fields["fingerprint"] = candidate_fingerprint.fingerprint
        else:
            unset_fields["fingerprint"] = ""
        if deterministic_fingerprint is not None:
            update_fields["deterministic_fingerprint"] = (
                deterministic_fingerprint.fingerprint
            )
        else:
            unset_fields["deterministic_fingerprint"] = ""

        unset_fields["matched_schema_id"] = ""

        update: dict[str, Any] = {"$set": update_fields}
        if unset_fields:
            update["$unset"] = unset_fields

        await database.documents.update_one({"_id": document_id}, update)

        return {
            "id": document_id,
            "status": "needs_review",
            "source": "llm",
            "confidence": confidence,
            "detected_headers": detection_result,
            **(
                {"fingerprint": candidate_fingerprint.fingerprint}
                if candidate_fingerprint is not None
                else {}
            ),
        }
    except Exception as error:
        await _mark_document_failed(database, document_id, reason=str(error))
        raise


async def _restore_header_structure_placeholders(
    header_structure: HeaderStructure,
    pii_document_id: str | None,
) -> HeaderStructure:
    if not pii_document_id:
        return header_structure

    serialized = json.dumps(header_structure, ensure_ascii=False)
    normalized = normalize_placeholder_tokens(serialized)
    if not contains_placeholder(normalized):
        return header_structure

    restored = await pii_restore(pii_document_id, json.loads(normalized))
    return validate_header_structure(restored)


def _raise_if_header_structure_has_placeholders(
    header_structure: HeaderStructure,
) -> None:
    serialized = json.dumps(header_structure, ensure_ascii=False)
    if contains_placeholder(normalize_placeholder_tokens(serialized)):
        raise PiiServiceError("PII restore left placeholders in header structure.")


async def _detect_known_schema(
    db: AsyncIOMotorDatabase,
    *,
    document: Document,
    deterministic_headers: dict[str, list[DetectedHeader]],
    deterministic_key_value_sections: dict[str, list[DetectedKeyValueSection]],
    deterministic_matrices: dict[str, list[DetectedMatrix]],
    deterministic_fingerprint: HeaderFingerprint | None,
) -> DocumentDetectionResponse | None:
    schema_match: SchemaMatch | None = None
    candidate_fingerprint = deterministic_fingerprint

    if deterministic_fingerprint is not None:
        schema_match = await match_schema_by_deterministic_fingerprint(
            deterministic_fingerprint,
            db=db,
            file_type=document.file_type,
        )

    if schema_match is None:
        try:
            candidate_fingerprint = build_deterministic_header_fingerprint(
                deterministic_headers,
                deterministic_matrices,
            )
        except ValueError:
            return None

        schema_match = await match_schema_by_fingerprint(
            candidate_fingerprint,
            db=db,
            file_type=document.file_type,
        )
        if schema_match is None:
            return None

    confidence = schema_match.similarity.score
    detection_result: DetectionResult = {
        "source": "schema",
        "header_structure": schema_match.schema.header_structure,
        "deterministic_headers": deterministic_headers,
        "deterministic_key_value_sections": deterministic_key_value_sections,
        "deterministic_matrices": deterministic_matrices,
        "flags": [],
        "confidence": confidence,
        "match": _format_schema_match(schema_match, candidate_fingerprint.fingerprint),
    }
    if deterministic_fingerprint is not None:
        detection_result[
            "deterministic_fingerprint"
        ] = deterministic_fingerprint.fingerprint

    update_fields: dict[str, Any] = {
        "status": "approved",
        "detected_headers": detection_result,
        "confidence": confidence,
        "fingerprint": schema_match.schema.fingerprint,
        "matched_schema_id": schema_match.schema.id,
    }
    if deterministic_fingerprint is not None:
        update_fields["deterministic_fingerprint"] = deterministic_fingerprint.fingerprint

    await db.documents.update_one(
        {"_id": document.id},
        {
            "$set": update_fields,
            "$unset": {"failure_reason": ""},
        },
    )

    response: DocumentDetectionResponse = {
        "id": document.id,
        "status": "approved",
        "source": "schema",
        "confidence": confidence,
        "detected_headers": detection_result,
        "fingerprint": schema_match.schema.fingerprint,
        "matched_schema_id": schema_match.schema.id,
    }
    if deterministic_fingerprint is not None:
        response["deterministic_fingerprint"] = deterministic_fingerprint.fingerprint

    return response


def _format_schema_match(
    schema_match: SchemaMatch,
    candidate_fingerprint: str,
) -> dict[str, Any]:
    similarity = schema_match.similarity
    return {
        "schema_id": schema_match.schema.id,
        "schema_name": schema_match.schema.name,
        "schema_fingerprint": schema_match.schema.fingerprint,
        "schema_deterministic_fingerprint": (
            schema_match.schema.deterministic_fingerprint
        ),
        "candidate_fingerprint": candidate_fingerprint,
        "score": similarity.score,
        "jaccard": similarity.jaccard,
        "containment": similarity.containment,
        "candidate_coverage": similarity.candidate_coverage,
        "schema_coverage": similarity.schema_coverage,
        "overlap_count": similarity.overlap_count,
    }


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

            title = table.get("title")
            unmatched_key = (sheet_name, _normalize_label(title or ""))
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


def _detect_deterministic_tabular_layout(
    path: Path,
) -> tuple[
    dict[str, list[DetectedHeader]],
    dict[str, list[DetectedMatrix]],
]:
    try:
        deterministic_headers: dict[str, list[DetectedHeader]] = {}
        deterministic_matrices: dict[str, list[DetectedMatrix]] = {}

        for sheet_name, grid in load_sheets(path).items():
            matrices = detect_matrices(grid)
            deterministic_matrices[sheet_name] = matrices
            deterministic_headers[sheet_name] = _deduplicate_headers(
                [
                    header
                    for header in detect_table_headers(grid)
                    if not _header_overlaps_matrix(header, matrices)
                ]
            )

        return deterministic_headers, deterministic_matrices
    except FileNotFoundError as error:
        raise StoredDocumentFileError(str(error)) from error
    except ValueError as error:
        raise InvalidDocumentFileError(str(error)) from error


def _detect_deterministic_key_value_sections(
    path: Path,
    deterministic_matrices: dict[str, list[DetectedMatrix]],
) -> dict[str, list[DetectedKeyValueSection]]:
    try:
        detected_sections = detect_key_value_sections(path)
        return {
            sheet_name: [
                section
                for section in sections
                if not _key_value_section_overlaps_matrix(
                    section,
                    deterministic_matrices.get(sheet_name, []),
                )
            ]
            for sheet_name, sections in detected_sections.items()
        }
    except FileNotFoundError as error:
        raise StoredDocumentFileError(str(error)) from error
    except ValueError as error:
        raise InvalidDocumentFileError(str(error)) from error


def _build_llm_cell_map(path: Path) -> dict[str, str]:
    try:
        return excel_path_to_cell_map(path)
    except FileNotFoundError as error:
        raise StoredDocumentFileError(str(error)) from error
    except ValueError as error:
        raise InvalidDocumentFileError(str(error)) from error


def _build_deterministic_layout_match_fingerprint(
    deterministic_headers: dict[str, list[DetectedHeader]],
    deterministic_key_value_sections: dict[str, list[DetectedKeyValueSection]],
    deterministic_matrices: dict[str, list[DetectedMatrix]],
) -> HeaderFingerprint | None:
    try:
        return build_deterministic_layout_fingerprint(
            deterministic_headers,
            deterministic_key_value_sections,
            deterministic_matrices,
        )
    except ValueError:
        return None


def _deduplicate_headers(headers: list[DetectedHeader]) -> list[DetectedHeader]:
    # The header detector returns one entry per row that looks like a header,
    # so an all-string data row (contact lists, address books) gets emitted as
    # a second "table" sharing the same title cell. Drop those repeats — they
    # bloat the deterministic fingerprint with data values and break schema
    # matching. Dedup by title_coordinate so distinct tables (different title
    # cells) survive; untitled tables aren't deduped because there's no key.
    seen_title_coordinates: set[str] = set()
    deduplicated: list[DetectedHeader] = []

    for header in headers:
        title_coordinate = header.get("title_coordinate")
        if title_coordinate:
            if title_coordinate in seen_title_coordinates:
                continue
            seen_title_coordinates.add(title_coordinate)
        deduplicated.append(header)

    return deduplicated


def _header_overlaps_matrix(
    header: DetectedHeader,
    matrices: list[DetectedMatrix],
) -> bool:
    row_index = header.get("row_index")
    if not isinstance(row_index, int):
        return False

    return any(
        matrix["header_row_index"] <= row_index <= matrix["data_end_row_index"]
        for matrix in matrices
    )


def _key_value_section_overlaps_matrix(
    section: DetectedKeyValueSection,
    matrices: list[DetectedMatrix],
) -> bool:
    section_rows = {section["row_index"]}
    for field in section.get("fields", []):
        coordinate = field.get("coordinate")
        if not isinstance(coordinate, str):
            continue

        match = re.search(r"\d+", coordinate)
        if match is not None:
            section_rows.add(int(match.group(0)))

    return any(
        any(
            matrix["header_row_index"] <= row_index <= matrix["data_end_row_index"]
            for row_index in section_rows
        )
        for matrix in matrices
    )


def apply_key_value_section_hints(
    header_structure: HeaderStructure,
    key_value_sections: dict[str, list[DetectedKeyValueSection]],
) -> HeaderStructure:
    sheets: list[dict[str, Any]] = []

    for sheet in header_structure["sheets"]:
        hints = key_value_sections.get(sheet["name"], [])
        if not hints:
            sheets.append(dict(sheet))
            continue

        hint_titles = {_normalize_label(hint["title"]) for hint in hints}
        existing_titles = {
            _normalize_label(section.get("title") or "")
            for section in sheet["sections"]
            if section.get("title")
        }
        if hint_titles.issubset(existing_titles):
            sheets.append(dict(sheet))
            continue

        sheets.append(
            {
                "name": sheet["name"],
                "sections": _apply_sheet_key_value_hints(sheet["sections"], hints),
            }
        )

    return {"sheets": sheets}


def _apply_sheet_key_value_hints(
    sections: list[dict[str, Any]],
    hints: list[DetectedKeyValueSection],
) -> list[dict[str, Any]]:
    hint_sections = [_key_value_hint_to_layout_section(hint) for hint in hints]
    hint_titles = {_normalize_label(hint["title"]) for hint in hints}
    output_sections: list[dict[str, Any]] = []
    inserted_hints = False

    for section in sections:
        if _is_placeholder_key_value_section_matched_by_hint(section, hints):
            continue

        section_title = _normalize_label(section.get("title") or "")
        if section_title in hint_titles:
            continue

        if not inserted_hints and _is_collapsed_key_value_section(section, hint_titles):
            output_sections.extend(hint_sections)
            inserted_hints = True
            continue

        output_sections.append(dict(section))

    if not inserted_hints:
        insert_index = _first_table_section_index(output_sections)
        output_sections = [
            *output_sections[:insert_index],
            *hint_sections,
            *output_sections[insert_index:],
        ]

    return output_sections


def _key_value_hint_to_layout_section(
    hint: DetectedKeyValueSection,
) -> dict[str, Any]:
    return {
        "type": "key_value",
        "title": hint["title"],
        "fields": [field["name"] for field in hint["fields"]],
    }


def _is_collapsed_key_value_section(
    section: dict[str, Any],
    hint_titles: set[str],
) -> bool:
    if section.get("type") != "key_value":
        return False

    fields = section.get("fields", [])
    if not isinstance(fields, list):
        return False

    matched_titles = {
        _normalize_label(field)
        for field in fields
        if isinstance(field, str) and _normalize_label(field) in hint_titles
    }
    return len(matched_titles) >= min(2, len(hint_titles))


def _is_placeholder_key_value_section_matched_by_hint(
    section: dict[str, Any],
    hints: list[DetectedKeyValueSection],
) -> bool:
    if section.get("type") != "key_value":
        return False

    title = section.get("title")
    if not isinstance(title, str):
        return False

    if not contains_placeholder(normalize_placeholder_tokens(title)):
        return False

    fields = section.get("fields", [])
    if not isinstance(fields, list):
        return False

    section_fields = {_normalize_label(field) for field in fields if isinstance(field, str)}
    if not section_fields:
        return False

    for hint in hints:
        hint_field_values = hint.get("fields", [])
        if not isinstance(hint_field_values, list):
            continue

        hint_fields = {
            _normalize_label(field["name"])
            for field in hint_field_values
            if isinstance(field.get("name"), str)
        }
        if not hint_fields:
            continue

        overlap_count = len(section_fields & hint_fields)
        required_overlap = min(2, len(section_fields), len(hint_fields))
        if overlap_count >= required_overlap:
            return True

    return False


def _first_table_section_index(sections: list[dict[str, Any]]) -> int:
    for index, section in enumerate(sections):
        if section.get("type") in ("table", "matrix"):
            return index

    return len(sections)


def apply_matrix_section_hints(
    header_structure: HeaderStructure,
    matrices: dict[str, list[DetectedMatrix]],
) -> HeaderStructure:
    sheets: list[dict[str, Any]] = []

    for sheet in header_structure["sheets"]:
        sheet_matrices = matrices.get(sheet["name"], [])
        sections = [dict(section) for section in sheet["sections"]]

        for matrix in sheet_matrices:
            matrix_section = _matrix_hint_to_layout_section(matrix)
            matching_index = _find_matrix_section_match(sections, matrix)
            if matching_index is None:
                sections.append(matrix_section)
            else:
                sections[matching_index] = matrix_section

        sheets.append({"name": sheet["name"], "sections": sections})

    return {"sheets": sheets}


def _matrix_hint_to_layout_section(matrix: DetectedMatrix) -> dict[str, Any]:
    section: dict[str, Any] = {
        "type": "matrix",
        "headers": [{"name": header["name"]} for header in matrix["headers"]],
    }
    title = matrix.get("title")
    if isinstance(title, str) and title.strip():
        section["title"] = title

    return section


def _find_matrix_section_match(
    sections: list[dict[str, Any]],
    matrix: DetectedMatrix,
) -> int | None:
    normalized_title = _normalize_label(matrix.get("title") or "")
    if normalized_title:
        for index, section in enumerate(sections):
            if _normalize_label(section.get("title") or "") == normalized_title:
                return index

    matrix_headers = {
        _normalize_label(header["name"])
        for header in matrix["headers"]
        if _normalize_label(header["name"])
    }
    if len(matrix_headers) < 2:
        return None

    best_index: int | None = None
    best_overlap = 0
    for index, section in enumerate(sections):
        if section.get("type") not in ("table", "matrix"):
            continue

        section_headers = _collect_header_names(section.get("headers", []))
        overlap = len(matrix_headers & section_headers)
        if overlap >= 2 and overlap > best_overlap:
            best_index = index
            best_overlap = overlap

    return best_index


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
            "$unset": {
                "confidence": "",
                "fingerprint": "",
                "matched_schema_id": "",
            },
        },
    )
