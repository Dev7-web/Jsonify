from __future__ import annotations

from typing import Any, TypedDict

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db import get_db
from app.llm.header_prompt import HeaderStructure, HeaderStructureParseError
from app.llm.header_prompt import validate_header_structure
from app.models import Document, DocumentSchema
from app.pipeline.fingerprint import build_deterministic_layout_fingerprint
from app.pipeline.fingerprint import build_header_structure_fingerprint


class DocumentApprovalResponse(TypedDict):
    id: str
    status: str
    schema_id: str
    matched_schema_id: str
    fingerprint: str
    deterministic_fingerprint: str | None


class ApproveHeadersError(RuntimeError):
    pass


class ApproveDocumentNotFoundError(ApproveHeadersError):
    pass


class InvalidApprovedHeadersError(ApproveHeadersError):
    pass


class SchemaPersistenceError(ApproveHeadersError):
    pass


async def approve_document_headers(
    document_id: str,
    *,
    name: str,
    header_structure: dict[str, Any],
    db: AsyncIOMotorDatabase | None = None,
) -> DocumentApprovalResponse:
    database = db if db is not None else get_db()

    schema_name = name.strip()
    if not schema_name:
        raise InvalidApprovedHeadersError("Schema name must not be empty.")

    record = await database.documents.find_one({"_id": document_id})
    if record is None:
        raise ApproveDocumentNotFoundError(f"Document not found: {document_id}")

    try:
        document = Document.model_validate(record)
        approved_header_structure = validate_header_structure(header_structure)
        fingerprint = build_header_structure_fingerprint(approved_header_structure)
        deterministic_fingerprint = _get_document_deterministic_fingerprint(document)
    except HeaderStructureParseError as error:
        raise InvalidApprovedHeadersError(str(error)) from error
    except ValueError as error:
        raise InvalidApprovedHeadersError(str(error)) from error

    schema_id = await _save_schema(
        database,
        document=document,
        name=schema_name,
        fingerprint=fingerprint,
        deterministic_fingerprint=deterministic_fingerprint,
        header_structure=approved_header_structure,
    )

    await database.documents.update_one(
        {"_id": document.id},
        {
            "$set": {
                "status": "approved",
                "fingerprint": fingerprint,
                "deterministic_fingerprint": deterministic_fingerprint,
                "matched_schema_id": schema_id,
            },
            "$unset": {"failure_reason": ""},
        },
    )

    return {
        "id": document.id,
        "status": "approved",
        "schema_id": schema_id,
        "matched_schema_id": schema_id,
        "fingerprint": fingerprint,
        "deterministic_fingerprint": deterministic_fingerprint,
    }


async def _save_schema(
    db: AsyncIOMotorDatabase,
    *,
    document: Document,
    name: str,
    fingerprint: str,
    deterministic_fingerprint: str | None,
    header_structure: HeaderStructure,
) -> str:
    schema = DocumentSchema(
        name=name,
        file_type=document.file_type,
        fingerprint=fingerprint,
        deterministic_fingerprint=deterministic_fingerprint,
        version=1,
        status="active",
        header_structure=header_structure,
        field_locators={},
    )
    payload = schema.model_dump(by_alias=True)

    try:
        await db.schemas.insert_one(payload)
        return schema.id
    except DuplicateKeyError:
        return await _update_existing_schema(
            db,
            name=name,
            file_type=document.file_type,
            fingerprint=fingerprint,
            deterministic_fingerprint=deterministic_fingerprint,
            header_structure=header_structure,
        )
    except Exception as error:
        raise SchemaPersistenceError("Could not save approved schema.") from error


async def _update_existing_schema(
    db: AsyncIOMotorDatabase,
    *,
    name: str,
    file_type: str,
    fingerprint: str,
    deterministic_fingerprint: str | None,
    header_structure: HeaderStructure,
) -> str:
    try:
        existing = await db.schemas.find_one({"fingerprint": fingerprint, "version": 1})
        if existing is None:
            raise SchemaPersistenceError("Could not find existing schema after duplicate key.")

        schema_id = existing["_id"]
        await db.schemas.update_one(
            {"_id": schema_id},
            {
                "$set": {
                    "name": name,
                    "file_type": file_type,
                    "deterministic_fingerprint": deterministic_fingerprint,
                    "status": "active",
                    "header_structure": header_structure,
                    "field_locators": {},
                }
            },
        )
        return schema_id
    except SchemaPersistenceError:
        raise
    except Exception as error:
        raise SchemaPersistenceError("Could not update existing approved schema.") from error


def _get_document_deterministic_fingerprint(document: Document) -> str | None:
    if document.deterministic_fingerprint:
        return document.deterministic_fingerprint

    detected_headers = document.detected_headers or {}
    stored_fingerprint = detected_headers.get("deterministic_fingerprint")
    if isinstance(stored_fingerprint, str) and stored_fingerprint:
        return stored_fingerprint

    deterministic_headers = detected_headers.get("deterministic_headers")
    deterministic_key_value_sections = detected_headers.get(
        "deterministic_key_value_sections",
    )
    if not isinstance(deterministic_headers, dict):
        return None

    try:
        return build_deterministic_layout_fingerprint(
            deterministic_headers,
            deterministic_key_value_sections
            if isinstance(deterministic_key_value_sections, dict)
            else {},
        ).fingerprint
    except ValueError:
        return None
