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
DEFAULT_SCHEMA_MATCH_THRESHOLD = 0.70
DEFAULT_MIN_JACCARD = 0.45
DEFAULT_MIN_SCHEMA_COVERAGE = 0.55
DEFAULT_MIN_OVERLAP_LABELS = 6

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
) -> HeaderFingerprint:
    labels: set[str] = set()

    for tables in deterministic_headers.values():
        for table in tables:
            _add_label(labels, table.get("title"))

            for column in table.get("columns", []):
                if isinstance(column, dict):
                    _add_label(labels, column.get("name"))

    return _build_fingerprint_from_labels(
        labels,
        empty_message="Deterministic headers must contain at least one table title or column.",
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
        is_strong_containment_match = (
            similarity.candidate_coverage >= 0.98
            and similarity.schema_coverage >= min_schema_coverage
        )
        if not is_high_jaccard_match and not is_strong_containment_match:
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
    )


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

            for header in section.get("headers", []):
                _collect_header_node_labels(labels, header)

    return labels


def _build_fingerprint_from_labels(
    labels: Iterable[str],
    *,
    empty_message: str,
) -> HeaderFingerprint:
    normalized_labels = frozenset(label for label in labels if label)
    if not normalized_labels:
        raise ValueError(empty_message)

    digest_source = "\n".join(sorted(normalized_labels)).encode("utf-8")
    digest = hashlib.sha256(digest_source).hexdigest()
    return HeaderFingerprint(
        fingerprint=f"{FINGERPRINT_VERSION}:{digest}",
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


def _normalize_label(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip().casefold()
