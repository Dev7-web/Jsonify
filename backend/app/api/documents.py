from pathlib import Path
from typing import Any
from uuid import uuid4

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, status
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
    DetectionInProgressError,
    DocumentNotFoundError,
    InvalidDocumentFileError,
    StoredDocumentFileError,
    UnsupportedDocumentTypeError,
    detect_document_headers,
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
    file_type: FileType
    status: DocumentStatus


class DocumentDetectHeadersResponse(BaseModel):
    id: str
    status: DocumentStatus
    source: str
    confidence: float
    detected_headers: dict[str, Any]
    fingerprint: str | None = None
    matched_schema_id: str | None = None


class DocumentApproveHeadersRequest(BaseModel):
    name: str = Field(min_length=1)
    header_structure: dict[str, Any]


class DocumentApproveHeadersResponse(BaseModel):
    id: str
    status: DocumentStatus
    schema_id: str
    matched_schema_id: str
    fingerprint: str


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
        file_type=document.file_type,
        status=document.status,
    )


@router.post("/{document_id}/detect-headers", response_model=DocumentDetectHeadersResponse)
async def detect_headers(document_id: str) -> DocumentDetectHeadersResponse:
    try:
        result = await detect_document_headers(document_id)
    except DocumentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except DetectionInProgressError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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

    return DocumentDetectHeadersResponse(**result)


@router.post("/{document_id}/approve-headers", response_model=DocumentApproveHeadersResponse)
async def approve_headers(
    document_id: str,
    payload: DocumentApproveHeadersRequest,
) -> DocumentApproveHeadersResponse:
    try:
        result = await approve_document_headers(
            document_id,
            name=payload.name,
            header_structure=payload.header_structure,
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
