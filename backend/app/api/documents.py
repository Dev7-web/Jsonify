import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import get_db
from ..llm.client import LLMCallError, LLMConfigurationError
from ..llm.header_prompt import HeaderStructureParseError
from ..models import Document, DocumentStatus, FileType
from ..pipeline.approve import (
    ApproveDocumentNotFoundError,
    InvalidApprovedHeadersError,
    SchemaPersistenceError,
    approve_document_headers,
)
from ..pipeline.detect import (
    DocumentNotFoundError,
    InvalidDocumentFileError,
    StoredDocumentFileError,
    UnsupportedDocumentTypeError,
    run_document_header_detection,
    start_document_header_detection,
)
from ..pipeline.extract import (
    ExtractDocumentNotFoundError,
    ExtractInvalidDocumentFileError,
    ExtractLocatorError,
    ExtractMissingSchemaLinkError,
    ExtractOutputNotFoundError,
    ExtractSchemaNotFoundError,
    ExtractStoredDocumentFileError,
    ExtractUnsupportedDocumentTypeError,
    extract_document_json,
    get_document_output_json,
)


router = APIRouter(prefix="/documents", tags=["documents"])

BACKEND_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_DIR_NAME = "uploads"
UPLOAD_DIR = BACKEND_ROOT / UPLOAD_DIR_NAME

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB

ALLOWED_FILE_TYPES: dict[str, FileType] = {
    ".xlsx": "xlsx",
    ".pdf": "pdf",
}

FILE_TYPE_MAGIC_BYTES: dict[FileType, bytes] = {
    "xlsx": b"PK\x03\x04",
    "pdf": b"%PDF",
}


class DocumentCreateResponse(BaseModel):
    id: str
    filename: str
    file_type: FileType
    status: DocumentStatus


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: FileType
    status: DocumentStatus
    detected_headers: dict[str, Any] | None = None
    review_draft: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    confidence: float | None = None
    fingerprint: str | None = None
    deterministic_fingerprint: str | None = None
    matched_schema_id: str | None = None
    failure_reason: str | None = None
    detection_progress: dict[str, Any] | None = None


class DocumentDetectHeadersResponse(BaseModel):
    id: str
    status: DocumentStatus
    source: str
    confidence: float
    detected_headers: dict[str, Any]
    fingerprint: str | None = None
    deterministic_fingerprint: str | None = None
    matched_schema_id: str | None = None


class DocumentDetectHeadersStartResponse(BaseModel):
    id: str
    status: DocumentStatus
    message: str
    event_url: str
    detection_progress: dict[str, Any] | None = None


class DocumentApproveHeadersRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    header_structure: dict[str, Any] | None = None


class DocumentReviewDraftRequest(BaseModel):
    schema_name: str = Field(min_length=1)
    header_structure: dict[str, Any]
    review_state: dict[str, Any]


class DocumentReviewDraftResponse(BaseModel):
    id: str
    review_draft: dict[str, Any]


class DocumentApproveHeadersResponse(BaseModel):
    id: str
    status: DocumentStatus
    schema_id: str
    matched_schema_id: str
    fingerprint: str
    deterministic_fingerprint: str | None = None


class DocumentExtractResponse(BaseModel):
    id: str
    status: DocumentStatus
    schema_id: str
    output_json: dict[str, Any]
    locators_created: bool


class DocumentJsonResponse(BaseModel):
    id: str
    status: DocumentStatus
    output_json: dict[str, Any]


def _format_detection_event_payload(document: Document) -> dict[str, Any]:
    progress = document.detection_progress or {}
    percent = progress.get("percent")
    stage = progress.get("stage")
    message = progress.get("message")

    if not isinstance(percent, int):
        percent = 100 if document.status in {"needs_review", "approved", "extracted"} else 0
    if not isinstance(stage, str):
        stage = "complete" if document.status in {"needs_review", "approved", "extracted"} else document.status
    if not isinstance(message, str):
        if document.status == "failed":
            message = document.failure_reason or "Header detection failed."
        elif document.status in {"needs_review", "approved", "extracted"}:
            message = "Header detection complete."
        else:
            message = "Header detection is running."

    payload: dict[str, Any] = {
        "id": document.id,
        "status": document.status,
        "percent": max(0, min(100, percent)),
        "stage": stage,
        "message": message,
    }
    updated_at = progress.get("updated_at")
    if isinstance(updated_at, str):
        payload["updated_at"] = updated_at
    if document.failure_reason:
        payload["failure_reason"] = document.failure_reason

    return payload


def _detection_event_name(document_status: DocumentStatus) -> str:
    if document_status == "failed":
        return "failed"
    if document_status in {"needs_review", "approved", "extracted"}:
        return "complete"
    return "progress"


def _format_sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def get_file_type(filename: str) -> FileType:
    extension = Path(filename).suffix.lower()
    file_type = ALLOWED_FILE_TYPES.get(extension)

    if file_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .xlsx and .pdf files are supported.",
        )

    return file_type


async def validate_magic_bytes(upload: UploadFile, file_type: FileType) -> None:
    expected = FILE_TYPE_MAGIC_BYTES[file_type]
    header = await upload.read(len(expected))
    await upload.seek(0)

    if not header.startswith(expected):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File contents do not match a valid .{file_type} file.",
        )


async def save_upload_file(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    bytes_written = 0

    async with aiofiles.open(destination, "wb") as output_file:
        while chunk := await upload.read(1024 * 1024):
            bytes_written += len(chunk)

            if bytes_written > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                )

            await output_file.write(chunk)


@router.post("", response_model=DocumentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_document(file: UploadFile) -> DocumentCreateResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must have a filename.",
        )

    original_filename = Path(file.filename).name
    file_type = get_file_type(original_filename)

    await validate_magic_bytes(file, file_type)

    document_id = str(uuid4())
    stored_filename = f"{document_id}_{original_filename}"
    relative_stored_path = f"{UPLOAD_DIR_NAME}/{stored_filename}"
    absolute_stored_path = UPLOAD_DIR / stored_filename

    document = Document(
        id=document_id,
        filename=original_filename,
        file_type=file_type,
        stored_path=relative_stored_path,
    )

    try:
        await save_upload_file(file, absolute_stored_path)
        await get_db().documents.insert_one(document.model_dump(by_alias=True))
    except HTTPException:
        if absolute_stored_path.exists():
            absolute_stored_path.unlink()
        raise
    except Exception as error:
        if absolute_stored_path.exists():
            absolute_stored_path.unlink()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save uploaded document.",
        ) from error
    finally:
        await file.close()

    return DocumentCreateResponse(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str) -> DocumentResponse:
    record = await get_db().documents.find_one({"_id": document_id})
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document not found: {document_id}",
        )

    document = Document.model_validate(record)
    return DocumentResponse(
        id=document.id,
        filename=document.filename,
        file_type=document.file_type,
        status=document.status,
        detected_headers=document.detected_headers,
        review_draft=document.review_draft,
        output_json=document.output_json,
        confidence=document.confidence,
        fingerprint=document.fingerprint,
        deterministic_fingerprint=document.deterministic_fingerprint,
        matched_schema_id=document.matched_schema_id,
        failure_reason=document.failure_reason,
        detection_progress=document.detection_progress,
    )


@router.post(
    "/{document_id}/detect-headers",
    response_model=DocumentDetectHeadersResponse | DocumentDetectHeadersStartResponse,
)
async def detect_headers(
    document_id: str,
    background_tasks: BackgroundTasks,
    response: Response,
) -> DocumentDetectHeadersResponse | DocumentDetectHeadersStartResponse:
    try:
        result = await start_document_header_detection(document_id)
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except UnsupportedDocumentTypeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except InvalidDocumentFileError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except StoredDocumentFileError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error
    except LLMConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except (LLMCallError, HeaderStructureParseError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error

    if result.get("status") == "detecting":
        if result.get("started"):
            background_tasks.add_task(run_document_header_detection, document_id)

        response.status_code = status.HTTP_202_ACCEPTED
        return DocumentDetectHeadersStartResponse(
            id=document_id,
            status="detecting",
            message=str(result.get("message") or "Header detection started."),
            event_url=f"/documents/{document_id}/detect-headers/events",
            detection_progress=result.get("detection_progress"),
        )

    return DocumentDetectHeadersResponse(**result)


@router.patch("/{document_id}/review-draft", response_model=DocumentReviewDraftResponse)
async def save_review_draft(
    document_id: str,
    payload: DocumentReviewDraftRequest,
) -> DocumentReviewDraftResponse:
    review_draft = {
        "schema_name": payload.schema_name,
        "header_structure": payload.header_structure,
        "review_state": payload.review_state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    result = await get_db().documents.update_one(
        {"_id": document_id},
        {"$set": {"review_draft": review_draft}},
    )
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document not found: {document_id}",
        )

    return DocumentReviewDraftResponse(
        id=document_id,
        review_draft=review_draft,
    )


@router.get("/{document_id}/detect-headers/events")
async def detect_header_events(document_id: str) -> StreamingResponse:
    record = await get_db().documents.find_one({"_id": document_id})
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document not found: {document_id}",
        )

    async def event_stream():
        last_heartbeat_at = time.monotonic()
        last_payload: str | None = None

        while True:
            record = await get_db().documents.find_one({"_id": document_id})
            if record is None:
                yield _format_sse_event(
                    "failed",
                    {
                        "id": document_id,
                        "status": "failed",
                        "percent": 100,
                        "stage": "failed",
                        "message": f"Document not found: {document_id}",
                    },
                )
                return

            document = Document.model_validate(record)
            payload = _format_detection_event_payload(document)
            serialized = json.dumps(payload, separators=(",", ":"))

            if serialized != last_payload:
                yield _format_sse_event(_detection_event_name(document.status), payload)
                last_payload = serialized
                last_heartbeat_at = time.monotonic()

            if document.status in {"needs_review", "approved", "extracted", "failed"}:
                return

            now = time.monotonic()
            if now - last_heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                last_heartbeat_at = now

            await asyncio.sleep(3)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{document_id}/approve-headers", response_model=DocumentApproveHeadersResponse)
async def approve_headers(
    document_id: str,
    payload: DocumentApproveHeadersRequest,
) -> DocumentApproveHeadersResponse:
    schema_name = payload.name
    header_structure = payload.header_structure

    if schema_name is None or header_structure is None:
        record = await get_db().documents.find_one({"_id": document_id})
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document not found: {document_id}",
            )

        review_draft = record.get("review_draft")
        if isinstance(review_draft, dict):
            if schema_name is None:
                draft_name = review_draft.get("schema_name")
                if isinstance(draft_name, str) and draft_name.strip():
                    schema_name = draft_name
            if header_structure is None:
                draft_header_structure = review_draft.get("header_structure")
                if isinstance(draft_header_structure, dict):
                    header_structure = draft_header_structure

    if schema_name is None or header_structure is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approve headers requires a header structure or a saved review draft.",
        )

    try:
        result = await approve_document_headers(
            document_id,
            name=schema_name,
            header_structure=header_structure,
        )
    except ApproveDocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except InvalidApprovedHeadersError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except SchemaPersistenceError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    return DocumentApproveHeadersResponse(**result)


@router.post("/{document_id}/extract", response_model=DocumentExtractResponse)
async def extract_json(document_id: str) -> DocumentExtractResponse:
    try:
        result = await extract_document_json(document_id)
    except ExtractDocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ExtractSchemaNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ExtractUnsupportedDocumentTypeError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ExtractMissingSchemaLinkError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ExtractInvalidDocumentFileError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ExtractLocatorError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ExtractStoredDocumentFileError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    return DocumentExtractResponse(**result)


@router.get("/{document_id}/json", response_model=DocumentJsonResponse)
async def get_json(document_id: str) -> DocumentJsonResponse:
    try:
        result = await get_document_output_json(document_id)
    except ExtractDocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except ExtractOutputNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    return DocumentJsonResponse(**result)
