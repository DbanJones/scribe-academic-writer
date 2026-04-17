"""Reference file dispatcher — extracts text from any supported file type."""

from __future__ import annotations

from pathlib import Path

from scribe.parsers import pdf, docx, xlsx


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".md", ".txt", ".csv"}


def extract_ref_text(path: Path, focus: str | None = None) -> str:
    """Extract text from a reference file.

    Args:
        path: Path to the reference file.
        focus: Optional focus hint, e.g. "pages 5-10" or "sheets: tile_agg, validation".
    """
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        page_range = _parse_page_focus(focus) if focus else None
        return pdf.extract(path, page_range=page_range)

    if suffix == ".docx":
        return docx.extract(path)

    if suffix == ".xlsx":
        sheet_names = _parse_sheet_focus(focus) if focus else None
        return xlsx.extract(path, sheet_names=sheet_names)

    if suffix in (".md", ".txt", ".csv"):
        return path.read_text(encoding="utf-8")

    return f"(Unsupported file type: {suffix})"


def extract_all_refs(refs_dir: Path) -> dict[str, str]:
    """Extract text from all reference files in a directory.

    Returns:
        Dict mapping relative path (e.g., "refs/data.xlsx") to extracted text.
    """
    result: dict[str, str] = {}
    if not refs_dir.exists():
        return result

    for path in sorted(refs_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            rel = path.relative_to(refs_dir.parent)
            result[str(rel)] = extract_ref_text(path)
        except Exception as e:
            rel = path.relative_to(refs_dir.parent)
            result[str(rel)] = f"(Error extracting {path.name}: {e})"

    return result


def extract_ref_to_cache(
    path: Path, cache_dir: Path, focus: str | None = None
) -> Path:
    """Extract a ref file to a cached .md file for SDK tool access.

    Returns the path to the cached markdown file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_name = path.stem + ".md"
    cache_path = cache_dir / cache_name

    text = extract_ref_text(path, focus=focus)
    cache_path.write_text(
        f"# Extracted from: {path.name}\n\n{text}",
        encoding="utf-8",
    )
    return cache_path


def _parse_page_focus(focus: str) -> str | None:
    """Extract page range from focus string like 'pages 5-10' or 'page 3'."""
    import re

    match = re.search(r"pages?\s*([\d,\s-]+)", focus, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _parse_sheet_focus(focus: str) -> list[str] | None:
    """Extract sheet names from focus string like 'sheets: tile_agg, validation'."""
    import re

    match = re.search(r"sheets?:\s*(.+)", focus, re.IGNORECASE)
    if match:
        return [s.strip() for s in match.group(1).split(",")]
    return None
