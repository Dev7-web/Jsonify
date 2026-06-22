from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


DocumentStatus = Literal[
    "uploaded",
    "detecting",
    "needs_review",
    "approved",
    "extracted",
    "failed",
]
FileType = Literal["xlsx", "pdf"]
SchemaStatus = Literal["draft", "active", "deprecated"]


def _new_id() -> str:
    return str(uuid4())


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=_new_id, alias="_id")
    filename: str
    file_type: FileType
    stored_path: str
    status: DocumentStatus = "uploaded"
    fingerprint: str | None = None
    deterministic_fingerprint: str | None = None
    matched_schema_id: str | None = None
    detected_headers: dict[str, Any] | None = None
    pii_document_id: str | None = None
    extraction_locators: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=_now_utc)


class DocumentSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=_new_id, alias="_id")
    name: str
    file_type: FileType
    fingerprint: str
    deterministic_fingerprint: str | None = None
    version: int = Field(default=1, ge=1)
    status: SchemaStatus = "active"
    header_structure: dict[str, Any]
    field_locators: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now_utc)
