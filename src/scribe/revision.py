"""Revision pipeline orchestration.

A separate pipeline from generation. Given an existing substantive draft,
revise it against the academic writing rules while preserving the author's
content, citations, and argument.

Stages:
    1. Parse:     split the document into heading-delimited sections.
    2. Audit:     Opus produces a structured DocumentAudit.
    3. Revise:    Sonnet revises each section in parallel using audit findings.
    4. Stitch:    Opus performs a light consistency pass across all sections.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import DocumentAudit
from scribe.parsers.sections import (
    DocumentSection,
    is_default_scaffold,
    looks_like_substantive_draft,
    split_into_sections,
)
from scribe.project import _read_document, Project
from scribe.sdk import StreamCallback
from scribe.stages.auditor import run_auditor
from scribe.stages.reviser import assemble_revised_document, run_reviser
from scribe.stages.revision_stitcher import run_revision_stitcher

logger = logging.getLogger(__name__)


class RevisionInputError(ValueError):
    """Raised when the input is unsuitable for revision (e.g. too short, scaffold)."""


def load_source_document(
    project: Project,
    source_file: Path | None = None,
) -> tuple[str, list[DocumentSection]]:
    """Load a source document for revision and split it into sections.

    Precedence for locating the source:
      1. Explicit source_file argument.
      2. project.source_document_path (root / source.{md,docx,doc,txt}).
      3. project.outline_path if it is substantive (fallback for existing
         projects where the user put a full draft in outline.md).

    Raises RevisionInputError if no suitable source is found or the
    content is a default scaffold / too short to revise.
    """
    candidate: Path | None = None

    if source_file:
        candidate = source_file
    else:
        # Try conventional source filenames first
        for ext in (".md", ".docx", ".doc", ".txt"):
            p = project.root / f"source{ext}"
            if p.exists():
                candidate = p
                break

        # Fall back to outline (some users may drop the draft in outline.md)
        if candidate is None and project.outline_path.exists():
            candidate = project.outline_path

    if candidate is None or not candidate.exists():
        raise RevisionInputError(
            f"No source document found in {project.root}. "
            "Upload a source.md / source.docx file, or pass a path."
        )

    text = _read_document(candidate)

    if is_default_scaffold(text):
        raise RevisionInputError(
            f"{candidate.name} is still the default scaffold from `scribe init`. "
            "Replace it with a real draft before revising."
        )

    if not looks_like_substantive_draft(text):
        words = len(text.split())
        raise RevisionInputError(
            f"{candidate.name} does not look like a substantive draft "
            f"({words} words; expected >=1,500 with headings and prose). "
            "Use the generation pipeline (`scribe run`) for outlines and sketches."
        )

    sections = split_into_sections(text)
    if len(sections) < 2:
        raise RevisionInputError(
            "Document has fewer than 2 heading-delimited sections; cannot "
            "revise section by section. Add Markdown headings (# Section) and retry."
        )

    return text, sections


async def run_revision_pipeline(
    project: Project,
    config: ScribeConfig,
    source_file: Path | None = None,
    stream_callback: StreamCallback | None = None,
    reuse_audit: bool = True,
) -> dict:
    """Run the full revision pipeline. Returns a summary dict.

    Args:
        reuse_audit: If True (default) and an audit already exists, reuse it
            rather than re-running the Opus auditor. Set False to force a
            fresh audit.
    """
    project.ensure_dirs()
    pipeline_start = time.time()

    # Stage 1: parse
    logger.info("Revision stage 1/4: parsing document")
    document_text, sections = load_source_document(project, source_file)

    # Keep an archived copy of the source for auditability
    archive = project.scribe_dir / "source_snapshot.md"
    archive.write_text(document_text, encoding="utf-8")

    # Build overall context to guide the reviser on coherence
    overall_context = _build_overall_context(sections)

    # Stage 2: audit (reuse cache if available)
    if reuse_audit and project.audit_path.exists():
        logger.info("Revision stage 2/4: reusing cached audit at %s",
                    project.audit_path)
        audit = DocumentAudit.load(project.audit_path)
    else:
        logger.info("Revision stage 2/4: auditing %d sections", len(sections))
        audit = await run_auditor(
            project=project,
            config=config,
            sections=sections,
            document_text=document_text,
            stream_callback=stream_callback,
        )

    # Stage 3: revise
    logger.info("Revision stage 3/4: revising sections (parallel=%d)",
                config.parallelism)
    results = await run_reviser(
        project=project,
        config=config,
        audit=audit,
        sections=sections,
        overall_context=overall_context,
        stream_callback=stream_callback,
    )

    # Pull out the title from the first H1, if any
    title = ""
    for section in sections:
        if section.level == 1:
            title = section.title
            break

    assembled = assemble_revised_document(results)

    # Stage 4: stitch
    logger.info("Revision stage 4/4: smoothing transitions")
    revised_path = await run_revision_stitcher(
        project=project,
        config=config,
        revised_document=assembled,
        stream_callback=stream_callback,
    )

    duration = time.time() - pipeline_start

    summary = {
        "title": title or audit.title,
        "source_path": str(archive),
        "revised_path": str(revised_path),
        "audit_path": str(project.audit_path),
        "audit_summary_path": str(project.audit_summary_path),
        "original_words": sum(s.word_count for s in sections),
        "revised_words": len(revised_path.read_text(encoding="utf-8").split()),
        "sections_total": len(sections),
        "sections_revised": sum(1 for r in results if not r.is_error),
        "sections_failed": sum(1 for r in results if r.is_error),
        "duration_s": duration,
        "audit_overall_issues": len(audit.overall_issues),
        "audit_verdict": audit.overall_verdict,
    }

    logger.info(
        "Revision complete in %.1fs: %d -> %d words, %d sections revised",
        duration, summary["original_words"], summary["revised_words"],
        summary["sections_revised"],
    )
    return summary


def _build_overall_context(sections: list[DocumentSection]) -> str:
    """Build a short outline of all section headings so each per-section
    reviser knows where its section fits in the document."""
    lines = ["DOCUMENT OUTLINE (for cross-section awareness):"]
    for s in sections:
        if s.id == "preamble":
            continue
        indent = "  " * max(0, s.level - 1)
        lines.append(f"{indent}- {s.title} ({s.word_count} words)")
    return "\n".join(lines)


def save_source_upload(
    project: Project,
    uploaded_file_path: Path,
    original_filename: str,
) -> Path:
    """Store an uploaded source document at the canonical location.

    Used by the web UI; preserves the original extension so _read_document
    can dispatch correctly.
    """
    project.ensure_dirs()
    ext = Path(original_filename).suffix.lower() or ".md"
    dest = project.root / f"source{ext}"

    # Clear out any previous source files so only one canonical source exists
    for e in (".md", ".txt", ".docx", ".doc"):
        p = project.root / f"source{e}"
        if p.exists() and p != dest:
            p.unlink()

    shutil.copy2(uploaded_file_path, dest)
    return dest
