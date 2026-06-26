from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError

from app.db import get_db
from app.models import DocumentSchema


FINGERPRINT_VERSION = "headers-v1"
DETERMINISTIC_FINGERPRINT_VERSION = "deterministic-v1"
DEFAULT_SCHEMA_MATCH_THRESHOLD = 0.70
DEFAULT_MIN_JACCARD = 0.45
DEFAULT_MIN_SCHEMA_COVERAGE = 0.55
DEFAULT_MIN_OVERLAP_LABELS = 6
DEFAULT_MIN_STRONG_SCHEMA_COVERAGE = 0.82
DEFAULT_MIN_STRONG_SCHEMA_OVERLAP = 12
DEFAULT_MIN_STRONG_SCHEMA_CANDIDATE_COVERAGE = 0.30

_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class HeaderFingerprint:
    fingerprint: str
    labels: frozenset[str]


@dataclass(frozen=True)
class FingerprintSimilarity:
    score: float
    jaccard: float
    containment: float
    candidate_coverage: float
    schema_coverage: float
    overlap_count: int


@dataclass(frozen=True)
class SchemaMatch:
    schema: DocumentSchema
    similarity: FingerprintSimilarity


def build_header_structure_fingerprint(header_structure: dict[str, Any]) -> str:
    return build_header_label_fingerprint(header_structure).fingerprint


def build_header_label_fingerprint(header_structure: dict[str, Any]) -> HeaderFingerprint:
    labels = collect_header_labels(header_structure)
    return _build_fingerprint_from_labels(
        labels,
        empty_message="Approved header structure must contain at least one header or field.",
    )


def collect_header_labels(header_structure: dict[str, Any]) -> frozenset[str]:
    return frozenset(_collect_header_labels(header_structure))


def build_deterministic_header_fingerprint(
    deterministic_headers: dict[str, list[dict[str, Any]]],
    deterministic_matrices: dict[str, list[dict[str, Any]]] | None = None,
) -> HeaderFingerprint:
    labels: set[str] = set()

    for tables in deterministic_headers.values():
        for table in tables:
            _add_label(labels, table.get("title"))

            for column in table.get("columns", []):
                if isinstance(column, dict):
                    _add_label(labels, column.get("name"))

    for matrices in (deterministic_matrices or {}).values():
        for matrix in matrices:
            _add_label(labels, matrix.get("title"))
            _add_label(labels, matrix.get("row_header"))

            for header in matrix.get("headers", []):
                if isinstance(header, dict):
                    _add_label(labels, header.get("name"))

    return _build_fingerprint_from_labels(
        labels,
        empty_message="Deterministic headers must contain at least one table title or column.",
    )


def build_deterministic_layout_fingerprint(
    deterministic_headers: dict[str, list[dict[str, Any]]],
    deterministic_key_value_sections: dict[str, list[dict[str, Any]]] | None = None,
    deterministic_matrices: dict[str, list[dict[str, Any]]] | None = None,
) -> HeaderFingerprint:
    labels: set[str] = set()
    key_value_rows = _key_value_rows_by_sheet(deterministic_key_value_sections or {})

    for sheet_name, sections in (deterministic_key_value_sections or {}).items():
        for section in sections:
            _add_stable_layout_label(labels, section.get("title"))

            for field in section.get("fields", []):
                if isinstance(field, dict):
                    _add_stable_layout_label(labels, field.get("name"))

    for sheet_name, tables in deterministic_headers.items():
        ignored_rows = key_value_rows.get(sheet_name, set())

        for table in tables:
            row_index = table.get("row_index")
            if isinstance(row_index, int) and row_index in ignored_rows:
                continue

            column_names = _stable_unique_labels(
                column.get("name")
                for column in table.get("columns", [])
                if isinstance(column, dict)
            )
            if len(column_names) < 2:
                continue

            _add_stable_layout_label(labels, table.get("title"))
            labels.update(column_names)

    for matrices in (deterministic_matrices or {}).values():
        for matrix in matrices:
            _add_stable_layout_label(labels, matrix.get("title"))
            _add_stable_layout_label(labels, matrix.get("row_header"))
            labels.update(
                _stable_unique_labels(
                    header.get("name")
                    for header in matrix.get("headers", [])
                    if isinstance(header, dict)
                )
            )

    return _build_fingerprint_from_labels(
        labels,
        empty_message=(
            "Deterministic layout must contain at least one stable field or header."
        ),
        version=DETERMINISTIC_FINGERPRINT_VERSION,
    )


def calculate_fingerprint_similarity(
    candidate: HeaderFingerprint,
    schema_fingerprint: HeaderFingerprint,
) -> FingerprintSimilarity:
    if not candidate.labels or not schema_fingerprint.labels:
        return FingerprintSimilarity(
            score=0.0,
            jaccard=0.0,
            containment=0.0,
            candidate_coverage=0.0,
            schema_coverage=0.0,
            overlap_count=0,
        )

    overlap = candidate.labels & schema_fingerprint.labels
    union = candidate.labels | schema_fingerprint.labels
    overlap_count = len(overlap)
    jaccard = overlap_count / len(union)
    candidate_coverage = overlap_count / len(candidate.labels)
    schema_coverage = overlap_count / len(schema_fingerprint.labels)
    containment = max(candidate_coverage, schema_coverage)
    score = max(jaccard, containment)

    return FingerprintSimilarity(
        score=round(score, 4),
        jaccard=round(jaccard, 4),
        containment=round(containment, 4),
        candidate_coverage=round(candidate_coverage, 4),
        schema_coverage=round(schema_coverage, 4),
        overlap_count=overlap_count,
    )


def find_best_schema_match(
    candidate: HeaderFingerprint,
    schemas: Iterable[DocumentSchema],
    *,
    threshold: float = DEFAULT_SCHEMA_MATCH_THRESHOLD,
    min_jaccard: float = DEFAULT_MIN_JACCARD,
    min_schema_coverage: float = DEFAULT_MIN_SCHEMA_COVERAGE,
    min_overlap_labels: int = DEFAULT_MIN_OVERLAP_LABELS,
    min_strong_schema_coverage: float = DEFAULT_MIN_STRONG_SCHEMA_COVERAGE,
    min_strong_schema_overlap: int = DEFAULT_MIN_STRONG_SCHEMA_OVERLAP,
    min_strong_schema_candidate_coverage: float = (
        DEFAULT_MIN_STRONG_SCHEMA_CANDIDATE_COVERAGE
    ),
) -> SchemaMatch | None:
    best_match: SchemaMatch | None = None

    for schema in schemas:
        try:
            schema_fingerprint = build_header_label_fingerprint(schema.header_structure)
        except ValueError:
            continue

        similarity = calculate_fingerprint_similarity(candidate, schema_fingerprint)
        required_overlap = min(
            min_overlap_labels,
            len(candidate.labels),
            len(schema_fingerprint.labels),
        )

        if similarity.score < threshold:
            continue

        if similarity.overlap_count < required_overlap:
            continue

        is_high_jaccard_match = similarity.jaccard >= min_jaccard
        is_strong_candidate_containment_match = (
            similarity.candidate_coverage >= 0.98
            and similarity.schema_coverage >= min_schema_coverage
        )
        is_strong_schema_coverage_match = (
            similarity.schema_coverage >= min_strong_schema_coverage
            and similarity.candidate_coverage >= min_strong_schema_candidate_coverage
            and similarity.overlap_count >= min_strong_schema_overlap
        )
        if (
            not is_high_jaccard_match
            and not is_strong_candidate_containment_match
            and not is_strong_schema_coverage_match
        ):
            continue

        if _is_better_match(similarity, best_match):
            best_match = SchemaMatch(schema=schema, similarity=similarity)

    return best_match


async def match_schema_by_fingerprint(
    candidate: HeaderFingerprint,
    *,
    db: AsyncIOMotorDatabase | None = None,
    file_type: str | None = None,
    threshold: float = DEFAULT_SCHEMA_MATCH_THRESHOLD,
    min_jaccard: float = DEFAULT_MIN_JACCARD,
    min_schema_coverage: float = DEFAULT_MIN_SCHEMA_COVERAGE,
    min_overlap_labels: int = DEFAULT_MIN_OVERLAP_LABELS,
    min_strong_schema_coverage: float = DEFAULT_MIN_STRONG_SCHEMA_COVERAGE,
    min_strong_schema_overlap: int = DEFAULT_MIN_STRONG_SCHEMA_OVERLAP,
    min_strong_schema_candidate_coverage: float = (
        DEFAULT_MIN_STRONG_SCHEMA_CANDIDATE_COVERAGE
    ),
) -> SchemaMatch | None:
    database = db if db is not None else get_db()

    query: dict[str, Any] = {"status": "active"}
    if file_type is not None:
        query["file_type"] = file_type

    schemas: list[DocumentSchema] = []
    async for record in database.schemas.find(query):
        try:
            schemas.append(DocumentSchema.model_validate(record))
        except ValidationError:
            continue

    return find_best_schema_match(
        candidate,
        schemas,
        threshold=threshold,
        min_jaccard=min_jaccard,
        min_schema_coverage=min_schema_coverage,
        min_overlap_labels=min_overlap_labels,
        min_strong_schema_coverage=min_strong_schema_coverage,
        min_strong_schema_overlap=min_strong_schema_overlap,
        min_strong_schema_candidate_coverage=min_strong_schema_candidate_coverage,
    )


async def match_schema_by_deterministic_fingerprint(
    candidate: HeaderFingerprint,
    *,
    db: AsyncIOMotorDatabase | None = None,
    file_type: str | None = None,
) -> SchemaMatch | None:
    database = db if db is not None else get_db()

    query: dict[str, Any] = {
        "status": "active",
        "deterministic_fingerprint": candidate.fingerprint,
    }
    if file_type is not None:
        query["file_type"] = file_type

    async for record in database.schemas.find(query):
        try:
            schema = DocumentSchema.model_validate(record)
        except ValidationError:
            continue

        return SchemaMatch(
            schema=schema,
            similarity=FingerprintSimilarity(
                score=1.0,
                jaccard=1.0,
                containment=1.0,
                candidate_coverage=1.0,
                schema_coverage=1.0,
                overlap_count=len(candidate.labels),
            ),
        )

    return None


def _is_better_match(
    similarity: FingerprintSimilarity,
    current_match: SchemaMatch | None,
) -> bool:
    if current_match is None:
        return True

    current = current_match.similarity
    return (similarity.score, similarity.overlap_count, similarity.jaccard) > (
        current.score,
        current.overlap_count,
        current.jaccard,
    )


def _collect_header_labels(header_structure: dict[str, Any]) -> set[str]:
    labels: set[str] = set()

    for sheet in header_structure.get("sheets", []):
        for section in sheet.get("sections", []):
            _add_label(labels, section.get("title"))

            for field in section.get("fields", []):
                _add_label(labels, field)

            _add_label(labels, section.get("row_header"))

            for header in section.get("headers", []):
                _collect_header_node_labels(labels, header)

    return labels


def _build_fingerprint_from_labels(
    labels: Iterable[str],
    *,
    empty_message: str,
    version: str = FINGERPRINT_VERSION,
) -> HeaderFingerprint:
    normalized_labels = frozenset(label for label in labels if label)
    if not normalized_labels:
        raise ValueError(empty_message)

    digest_source = "\n".join(sorted(normalized_labels)).encode("utf-8")
    digest = hashlib.sha256(digest_source).hexdigest()
    return HeaderFingerprint(
        fingerprint=f"{version}:{digest}",
        labels=normalized_labels,
    )


def _collect_header_node_labels(labels: set[str], header: dict[str, Any]) -> None:
    _add_label(labels, header.get("name"))

    for subheader in header.get("subheaders", []):
        _collect_header_node_labels(labels, subheader)


def _add_label(labels: set[str], value: Any) -> None:
    if not isinstance(value, str):
        return

    label = _normalize_label(value)
    if label:
        labels.add(label)


def _add_stable_layout_label(labels: set[str], value: Any) -> None:
    if not isinstance(value, str):
        return

    label = _normalize_label(value)
    if _is_stable_layout_label(label):
        labels.add(label)


def _stable_unique_labels(values: Iterable[Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        label = _normalize_label(value)
        if label in seen or not _is_stable_layout_label(label):
            continue

        seen.add(label)
        labels.append(label)

    return labels


def _key_value_rows_by_sheet(
    key_value_sections: dict[str, list[dict[str, Any]]],
) -> dict[str, set[int]]:
    rows_by_sheet: dict[str, set[int]] = {}

    for sheet_name, sections in key_value_sections.items():
        rows: set[int] = set()

        for section in sections:
            row_index = section.get("row_index")
            if isinstance(row_index, int):
                rows.add(row_index)

            for field in section.get("fields", []):
                if not isinstance(field, dict):
                    continue

                coordinate = field.get("coordinate")
                if isinstance(coordinate, str):
                    row = _coordinate_row(coordinate)
                    if row is not None:
                        rows.add(row)

        rows_by_sheet[sheet_name] = rows

    return rows_by_sheet


def _coordinate_row(coordinate: str) -> int | None:
    match = re.search(r"\d+", coordinate)
    if match is None:
        return None

    return int(match.group(0))


def _is_stable_layout_label(label: str) -> bool:
    if not label:
        return False

    if len(label) > 80:
        return False

    return any(character.isalpha() for character in label)


def _normalize_label(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip().casefold()
