"""Dump exactly what gets sent to the LLM for header detection.

This reproduces the production pipeline step-by-step:

    load_sheets(xlsx)
        -> sheets_to_cell_map        (same as app/pipeline/detect.py)
        -> pii_redact                (optional, same PII service the app uses)
        -> cell_map_to_llm_text      (flattened workbook text)
        -> build_header_detection_user_prompt + SYSTEM_PROMPT

It writes the system prompt, the user prompt, and the flattened workbook
to an output folder so you can show the client precisely what leaves the
backend. It does NOT call Gemini -- nothing is generated, only captured.

Usage (from the backend/ folder, with the venv active):

    python scripts/dump_llm_payload.py <path-to-xlsx> [--no-redact] [--out <folder>]

Examples:

    python scripts/dump_llm_payload.py uploads/sample.xlsx
    python scripts/dump_llm_payload.py uploads/sample.xlsx --no-redact
    python scripts/dump_llm_payload.py uploads/sample.xlsx --out client_review
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_ROOT, get_pii_enabled
from app.llm.header_prompt import (
    SYSTEM_PROMPT,
    build_header_detection_user_prompt,
)
from app.parsing.excel_loader import load_sheets
from app.pii.adapt import cell_map_to_llm_text, sheets_to_cell_map
from app.pii.client import pii_redact


async def build_payload(xlsx_path: Path, *, redact: bool) -> dict[str, str]:
    sheets = load_sheets(xlsx_path)
    cell_map = sheets_to_cell_map(sheets)

    redacted_note = "PII redaction was NOT applied (raw values shown)."
    map_for_llm = cell_map
    if redact:
        if not get_pii_enabled():
            redacted_note = (
                "PII_ENABLED=false in .env, so the app would NOT redact. "
                "Raw values shown. Use the app's real config to match production."
            )
        else:
            document_id, redacted = await pii_redact(cell_map)
            if not isinstance(redacted, dict):
                raise SystemExit("PII service returned an unexpected (non-object) payload.")
            map_for_llm = redacted
            redacted_note = f"PII redaction applied. document_id={document_id}"

    flattened_text = cell_map_to_llm_text(map_for_llm)
    user_prompt = build_header_detection_user_prompt(flattened_text)

    return {
        "system_prompt": SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "flattened_workbook": flattened_text,
        "redaction_note": redacted_note,
        "cell_count": str(len(map_for_llm)),
    }


def write_outputs(out_dir: Path, source: Path, payload: dict[str, str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "system_prompt.txt").write_text(payload["system_prompt"], encoding="utf-8")
    (out_dir / "user_prompt.txt").write_text(payload["user_prompt"], encoding="utf-8")
    (out_dir / "flattened_workbook.txt").write_text(
        payload["flattened_workbook"], encoding="utf-8"
    )

    combined = (
        "===== WHAT IS SENT TO THE LLM (header detection) =====\n"
        f"Source workbook : {source}\n"
        f"Cells included  : {payload['cell_count']}\n"
        f"Redaction       : {payload['redaction_note']}\n"
        "\n"
        "----- SYSTEM PROMPT -----\n"
        f"{payload['system_prompt']}\n"
        "----- USER PROMPT -----\n"
        f"{payload['user_prompt']}\n"
    )
    (out_dir / "what_is_sent_to_llm.txt").write_text(combined, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump the exact prompt sent to the LLM for header detection."
    )
    parser.add_argument("xlsx", help="Path to the .xlsx workbook.")
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Skip the PII service (show raw values). Default sends through PII redaction.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output folder (default: backend/llm_payloads/<workbook-stem>).",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.is_absolute():
        xlsx_path = (Path.cwd() / xlsx_path).resolve()
    if not xlsx_path.is_file():
        raise SystemExit(f"Workbook not found: {xlsx_path}")

    out_dir = (
        Path(args.out)
        if args.out
        else BACKEND_ROOT / "llm_payloads" / xlsx_path.stem
    )

    payload = asyncio.run(build_payload(xlsx_path, redact=not args.no_redact))
    write_outputs(out_dir, xlsx_path, payload)

    print(f"Wrote LLM payload to: {out_dir}")
    print(f"  - what_is_sent_to_llm.txt  (system + user combined, show this to the client)")
    print(f"  - system_prompt.txt")
    print(f"  - user_prompt.txt")
    print(f"  - flattened_workbook.txt   ({payload['cell_count']} cells)")
    print(f"  {payload['redaction_note']}")


if __name__ == "__main__":
    main()
