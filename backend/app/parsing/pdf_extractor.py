from pathlib import Path
from typing import Any
from pypdf import PdfReader


def extract_text_from_pdf(pdf_path: Path) -> list[dict[str, Any]]:
    """Extracts text page by page from a PDF file.

    Returns a list of dicts: [{"page": 1, "text": "..."}]
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    reader = PdfReader(pdf_path)
    pages_content = []

    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages_content.append({
            "page": i + 1,
            "text": text.strip()
        })

    return pages_content
