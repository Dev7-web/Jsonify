"""Diagnose why a document is (or isn't) matching a saved schema.

Usage:
    python -m scripts.diagnose_schema_match <document_id>
    python -m scripts.diagnose_schema_match --filename "826 Dollar Tree #1308 (6).xlsx"
    python -m scripts.diagnose_schema_match --latest

Reads the document, rebuilds its deterministic fingerprint the same way
detect_document_headers does, then walks every active schema in the database
and prints similarity score + which threshold check passed or failed.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from app.config import BACKEND_ROOT
from app.db import get_db
from app.models import Document, DocumentSchema
from app.parsing.excel_loader import load_sheets
from app.parsing.form_detector import detect_key_value_sections
from app.parsing.header_detector import detect_table_headers
from app.pipeline.detect import _deduplicate_headers
from app.pipeline.fingerprint import (
    DEFAULT_MIN_JACCARD,
    DEFAULT_MIN_OVERLAP_LABELS,
    DEFAULT_MIN_SCHEMA_COVERAGE,
    DEFAULT_SCHEMA_MATCH_THRESHOLD,
    build_deterministic_header_fingerprint,
    build_deterministic_layout_fingerprint,
    build_header_label_fingerprint,
    calculate_fingerprint_similarity,
)


async def find_target_document(
    db,
    *,
    document_id: str | None,
    filename: str | None,
    latest: bool,
) -> dict | None:
    if document_id:
        return await db.documents.find_one({"_id": document_id})
    if filename:
        return await db.documents.find_one(
            {"filename": filename},
            sort=[("created_at", -1)],
        )
    if latest:
        return await db.documents.find_one(sort=[("created_at", -1)])
    return None


def compute_deterministic_fingerprint(document: Document):
    workbook_path = (BACKEND_ROOT / document.stored_path).resolve()
    if not workbook_path.is_file():
        raise RuntimeError(f"Stored file not found at {workbook_path}")

    sheets = load_sheets(workbook_path)
    deterministic = {
        sheet_name: _deduplicate_headers(detect_table_headers(grid))
        for sheet_name, grid in sheets.items()
    }
    deterministic_key_value_sections = detect_key_value_sections(workbook_path)

    label_summary = {
        sheet_name: [
            {
                "title": table.get("title"),
                "columns": [col.get("name") for col in table.get("columns", [])],
            }
            for table in tables
        ]
        for sheet_name, tables in deterministic.items()
    }

    return (
        build_deterministic_layout_fingerprint(
            deterministic,
            deterministic_key_value_sections,
        ),
        build_deterministic_header_fingerprint(deterministic),
        label_summary,
    )


def assess_match(candidate, schema, schema_fp):
    similarity = calculate_fingerprint_similarity(candidate, schema_fp)
    required_overlap = min(
        DEFAULT_MIN_OVERLAP_LABELS,
        len(candidate.labels),
        len(schema_fp.labels),
    )
    score_check = f"score >= {DEFAULT_SCHEMA_MATCH_THRESHOLD}"
    checks = {
        score_check: similarity.score >= DEFAULT_SCHEMA_MATCH_THRESHOLD,
        f"overlap >= {required_overlap}": similarity.overlap_count >= required_overlap,
        "jaccard >= 0.45": similarity.jaccard >= DEFAULT_MIN_JACCARD,
        "candidate_cov >= 0.98": similarity.candidate_coverage >= 0.98,
        "schema_cov >= 0.55": similarity.schema_coverage >= DEFAULT_MIN_SCHEMA_COVERAGE,
    }
    is_high_jaccard = checks["jaccard >= 0.45"]
    is_strong_containment = (
        checks["candidate_cov >= 0.98"] and checks["schema_cov >= 0.55"]
    )
    would_match = (
        checks[score_check]
        and checks[f"overlap >= {required_overlap}"]
        and (is_high_jaccard or is_strong_containment)
    )
    return similarity, checks, would_match


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("document_id", nargs="?", help="Document _id to inspect")
    group.add_argument("--filename", help="Find the most recent doc with this filename")
    group.add_argument(
        "--latest", action="store_true", help="Use the most recently created doc"
    )
    args = parser.parse_args()

    db = get_db()

    record = await find_target_document(
        db,
        document_id=args.document_id,
        filename=args.filename,
        latest=args.latest,
    )
    if record is None:
        print("No matching document found.")
        return

    try:
        document = Document.model_validate(record)
    except ValidationError as error:
        print(f"Could not parse document record: {error}")
        return

    print(f"=== Document {document.id} ===")
    print(f"  filename:        {document.filename}")
    print(f"  status:          {document.status}")
    print(f"  fingerprint:     {document.fingerprint}")
    print(f"  matched_schema:  {document.matched_schema_id}")
    print()

    try:
        candidate_fp, legacy_candidate_fp, label_summary = compute_deterministic_fingerprint(
            document,
        )
    except (RuntimeError, ValueError) as error:
        print(f"Could not build deterministic fingerprint: {error}")
        return

    print(
        f"--- Deterministic layout fingerprint ({len(candidate_fp.labels)} labels) ---"
    )
    print(f"  {candidate_fp.fingerprint}")
    print()
    print(
        f"--- Legacy table pre-scan fingerprint ({len(legacy_candidate_fp.labels)} labels) ---"
    )
    print(f"  {legacy_candidate_fp.fingerprint}")
    print()
    print("--- Deterministic table pre-scan summary ---")
    for sheet_name, tables in label_summary.items():
        print(f"  Sheet: {sheet_name}")
        for table in tables:
            title = table["title"] or "(untitled)"
            print(f"    Table: {title}")
            for col in table["columns"]:
                print(f"      - {col}")
    print()
    print(f"  Normalised labels:")
    for label in sorted(candidate_fp.labels):
        print(f"    {label!r}")
    print()

    cursor = db.schemas.find({"status": "active", "file_type": document.file_type})
    schemas: list[DocumentSchema] = []
    async for schema_record in cursor:
        try:
            schemas.append(DocumentSchema.model_validate(schema_record))
        except ValidationError:
            continue

    if not schemas:
        print(
            f"No active schemas exist for file_type={document.file_type!r}. "
            "Approve at least one document of this layout first."
        )
        return

    print(f"--- {len(schemas)} active schema(s) to compare against ---\n")
    best_score = 0.0
    best_id: str | None = None
    for schema in schemas:
        try:
            schema_fp = build_header_label_fingerprint(schema.header_structure)
        except ValueError:
            print(f"Schema {schema.id} ({schema.name}): unusable (no labels)\n")
            continue

        similarity, checks, would_match = assess_match(candidate_fp, schema, schema_fp)
        exact_deterministic_match = schema.deterministic_fingerprint == candidate_fp.fingerprint
        verdict = (
            "EXACT DETERMINISTIC MATCH"
            if exact_deterministic_match
            else "WOULD MATCH"
            if would_match
            else "no match"
        )
        marker = "[YES]" if exact_deterministic_match or would_match else "[ no]"

        print(f"  {marker} {schema.id} — {schema.name!r} ({verdict})")
        print(f"    schema deterministic fp={schema.deterministic_fingerprint}")
        print(
            f"    score={similarity.score} "
            f"jaccard={similarity.jaccard} "
            f"containment={similarity.containment}"
        )
        print(
            f"    overlap={similarity.overlap_count}  "
            f"cand_cov={similarity.candidate_coverage}  "
            f"schema_cov={similarity.schema_coverage}"
        )
        print(
            f"    candidate_labels={len(candidate_fp.labels)}  "
            f"schema_labels={len(schema_fp.labels)}"
        )
        for check, passed in checks.items():
            print(f"      [{'OK' if passed else '  '}] {check}")
        print(f"    schema labels ({len(schema_fp.labels)}):")
        for label in sorted(schema_fp.labels):
            marker = "  *" if label in candidate_fp.labels else "   "
            print(f"     {marker} {label!r}")
        only_in_candidate = candidate_fp.labels - schema_fp.labels
        if only_in_candidate:
            print(f"    candidate labels NOT in this schema ({len(only_in_candidate)}):")
            for label in sorted(only_in_candidate):
                print(f"        {label!r}")
        print()

        if similarity.score > best_score:
            best_score = similarity.score
            best_id = schema.id

    print(
        f"=== Verdict: highest-scoring schema was {best_id} "
        f"with score {best_score} ==="
    )


if __name__ == "__main__":
    asyncio.run(main())
