from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.config import BACKEND_ROOT
from app.db import get_db
from app.llm.client import gemini_call
from app.models import Document
from app.parsing.pdf_extractor import extract_text_from_pdf

logger = logging.getLogger(__name__)


class _VerificationStatus(str, Enum):
    verified = "verified"
    mismatch = "mismatch"
    not_found = "not_found"


class _VerificationField(BaseModel):
    """Structured-output schema for one verified field. Forcing Gemini to
    return JSON matching this shape eliminates the free-form parse failures
    (and the pointless, temperature-0 deterministic retry storm) that used to
    make verification both slow and unreliable."""

    field_path: str
    status: _VerificationStatus
    extracted_value: str
    found_value: str
    citation: str


class VerificationError(RuntimeError):
    pass


class VerificationDocumentNotFoundError(VerificationError):
    pass


class VerificationMissingDataError(VerificationError):
    pass


def _flatten_json(y: dict[str, Any]) -> dict[str, Any]:
    out = {}

    def flatten(x: Any, name: str = ""):
        if isinstance(x, dict):
            for a in x:
                flatten(x[a], name + a + ".")
        elif isinstance(x, list):
            for i, a in enumerate(x):
                flatten(a, name + str(i) + ".")
        else:
            out[name[:-1]] = x

    flatten(y)
    return out


async def run_document_verification(
    document_id: str,
    *,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    database = db if db is not None else get_db()

    document_record = await database.documents.find_one({"_id": document_id})
    if document_record is None:
        raise VerificationDocumentNotFoundError(f"Document not found: {document_id}")

    document = Document.model_validate(document_record)

    if not document.output_json:
        raise VerificationMissingDataError(
            "No extracted data found. Run extraction before verification."
        )

    if not document.supporting_files:
        raise VerificationMissingDataError(
            "No supporting PDF files uploaded. Upload supporting files before verification."
        )

    # Set status to verifying
    await database.documents.update_one(
        {"_id": document_id},
        {"$set": {"status": "verifying"}},
    )

    try:
        # Load and parse PDFs concurrently
        async def _load_pdf(file_info: dict[str, Any]) -> dict[str, Any] | None:
            filename = file_info["filename"]
            stored_path = file_info["stored_path"]
            absolute_path = (BACKEND_ROOT / stored_path).resolve()

            if not absolute_path.exists():
                logger.warning("Supporting PDF file not found at %s", absolute_path)
                return None

            pages = await asyncio.to_thread(extract_text_from_pdf, absolute_path)
            return {"filename": filename, "pages": pages}

        pdf_results = await asyncio.gather(
            *(_load_pdf(f) for f in document.supporting_files)
        )
        pdf_contents = [p for p in pdf_results if p is not None]

        if not pdf_contents:
            raise VerificationMissingDataError(
                "Could not read any of the uploaded supporting PDF files."
            )

        # Format user prompt for PDF text (shared across all chunk requests)
        formatted_pdfs = ""
        for pdf in pdf_contents:
            formatted_pdfs += f"\n=========================================\n"
            formatted_pdfs += f"SUPPORTING DOCUMENT: {pdf['filename']}\n"
            formatted_pdfs += f"=========================================\n"
            for page in pdf["pages"]:
                formatted_pdfs += f"\n--- Page {page['page']} ---\n"
                formatted_pdfs += page["text"]
                formatted_pdfs += "\n"

        # Flatten the JSON and chunk it. The full PDF text is re-sent with
        # every chunk, so it dominates each request's input cost; keeping the
        # chunk large minimises how many times we pay for that PDF prefill
        # while staying well within the model's output limit.
        flattened_data = _flatten_json(document.output_json)
        all_items = list(flattened_data.items())
        chunk_size = 25
        chunks = [all_items[i : i + chunk_size] for i in range(0, len(all_items), chunk_size)]

        async def _verify_chunk(chunk_items: list[tuple[str, Any]]) -> list[dict[str, Any]]:
            chunk_json = {k: v for k, v in chunk_items}
            
            user_prompt = f"""EXTRACTED EXCEL JSON DATA (Subset):
```json
{json.dumps(chunk_json, indent=2)}
```

SUPPORTING PDF DOCUMENTS TEXT:
{formatted_pdfs}
"""

            system_prompt = """You are an expert document verification assistant.
Your task is to verify whether the data extracted from an Excel document matches the information present in the provided supporting PDF documents.
You will receive:
1. A subset of the extracted JSON data from the Excel sheet.
2. The text content of the supporting PDFs, page by page.

For each field in the provided JSON subset, locate the matching information in the supporting PDFs.
Categorize each field's verification status as one of:
- "verified": The value exactly matches (or is semantically equivalent to) the value found in the PDFs.
- "mismatch": The field is found in the PDFs, but the value is different.
- "not_found": The field or its value cannot be found anywhere in the PDFs.

For each field, provide:
- field_path: The full path to the field (exactly as provided in the JSON key).
- status: "verified", "mismatch", or "not_found".
- extracted_value: The value from the Excel JSON.
- found_value: The value found in the PDF (if "verified" or "mismatch").
- citation: The source filename, page number, and surrounding context text from the PDF supporting this status. (e.g. "invoice.pdf (Page 1): 'Total due: $150.00'")

Your output MUST be a valid JSON array matching this structure exactly (do not wrap it in an object):
[
  {
    "field_path": "string",
    "status": "verified" | "mismatch" | "not_found",
    "extracted_value": "any",
    "found_value": "any",
    "citation": "string"
  }
]
"""
            # Structured output guarantees the response conforms to the
            # schema, so a single call parses reliably. Retrying a
            # temperature-0 prompt was pointless (it reproduces the same
            # output) and only multiplied the wall-clock time.
            response_text = await gemini_call(
                system=system_prompt,
                user=user_prompt,
                response_mime_type="application/json",
                response_schema=list[_VerificationField],
            )
            try:
                return json.loads(response_text)
            except json.JSONDecodeError as error:
                logger.warning("Verification JSON decode failed: %s", error)
                raise VerificationError(
                    f"Failed to parse verification JSON. Error: {error}"
                ) from error

        # Run verification chunks concurrently
        chunk_results = await asyncio.gather(
            *(_verify_chunk(chunk) for chunk in chunks)
        )

        all_fields = []
        for fields_array in chunk_results:
            all_fields.extend(fields_array)

        # Compute summary
        total_fields = len(all_fields)
        verified_fields = sum(1 for f in all_fields if f.get("status") == "verified")
        mismatched_fields = sum(1 for f in all_fields if f.get("status") == "mismatch")
        not_found_fields = sum(1 for f in all_fields if f.get("status") == "not_found")
        percentage = (verified_fields / total_fields * 100) if total_fields > 0 else 0.0

        # Generate textual summary
        issues = [f for f in all_fields if f.get("status") != "verified"]
        if issues:
            issues_summary = "\n".join(
                f"- {f.get('field_path')}: {f.get('status')} (Extracted: {f.get('extracted_value')}, Found: {f.get('found_value')})"
                for f in issues
            )
            summary_user_prompt = f"""Based on the following verification errors, write a 2-3 sentence summary explaining what was mismatched or not found.
Total fields checked: {total_fields}
Verified: {verified_fields}

Errors:
{issues_summary}"""
        else:
            summary_user_prompt = f"All {total_fields} fields were successfully verified against the supporting documents. Write a 1 sentence confirmation."

        summary_system_prompt = """You are a helpful assistant. Write a brief, professional summary of the document verification results. Do not output JSON. Just output plain text."""
        
        textual_summary = await gemini_call(
            system=summary_system_prompt,
            user=summary_user_prompt,
        )

        report = {
            "summary": {
                "total_fields": total_fields,
                "verified_fields": verified_fields,
                "mismatched_fields": mismatched_fields,
                "not_found_fields": not_found_fields,
                "percentage": percentage,
                "textual_summary": textual_summary.strip()
            },
            "fields": all_fields
        }

        # Save to database
        await database.documents.update_one(
            {"_id": document_id},
            {
                "$set": {"status": "verified", "verification_report": report},
                "$unset": {"failure_reason": ""},
            },
        )

        return report

    except Exception as error:
        logger.error("Verification pipeline failed: %s", error, exc_info=True)
        # Update status back to failed or reset to extracted
        await database.documents.update_one(
            {"_id": document_id},
            {
                "$set": {
                    "status": "failed",
                    "failure_reason": f"Verification failed: {str(error)}",
                }
            },
        )
        raise VerificationError(f"Verification failed: {str(error)}") from error
