"""PDF text extraction using pymupdf."""

from __future__ import annotations

from pathlib import Path

import pymupdf


def extract(path: Path, page_range: str | None = None) -> str:
    """Extract text from a PDF file.

    Args:
        path: Path to the PDF file.
        page_range: Optional page range like "5-10" or "3". 1-indexed.
    """
    doc = pymupdf.open(str(path))
    pages: list[int] = []

    if page_range:
        for part in page_range.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                pages.extend(range(int(start) - 1, int(end)))
            else:
                pages.append(int(part) - 1)
    else:
        pages = list(range(len(doc)))

    parts: list[str] = []
    for page_num in pages:
        if 0 <= page_num < len(doc):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                parts.append(f"--- Page {page_num + 1} ---\n{text}")

    doc.close()
    return "\n\n".join(parts)
