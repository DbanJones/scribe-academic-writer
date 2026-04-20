"""Expansion pipeline orchestration.

Given an existing substantive draft and a target word count (or multiplier),
produce a longer, deeper version that preserves every claim, citation, figure,
and number from the source.

Stages:
    1. Parse:   split into heading-delimited sections
    2. Plan:    Opus produces a per-section DocumentExpansion plan
    3. Expand:  Sonnet expands each section in parallel
    4. Smooth:  Opus does a light consistency pass (reused from revision_stitcher)
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import DocumentExpansion
from scribe.parsers.sections import (
    DocumentSection,
    is_default_scaffold,
    looks_like_substantive_draft,
    split_into_sections,
)
from scribe.parsers.refs import extract_all_refs
from scribe.project import _read_document, Project
from scribe.sdk import StreamCallback
from scribe.stages.expander import (
    ExpansionRunResult,
    assemble_expanded_document,
    run_expander,
)
from scribe.stages.expander_planner import run_expansion_planner

logger = logging.getLogger(__name__)


class ExpansionInputError(ValueError):
    """Raised when the input is unsuitable for expansion."""


def load_source_document_for_expansion(
    project: Project,
    source_file: Path | None = None,
) -> tuple[str, list[DocumentSection]]:
    """Load a source document for expansion and split it into sections.

    Precedence:
      1. Explicit source_file argument.
      2. project root / source.{md,docx,doc,txt}
      3. project.outline_path if it's substantive.
    """
    candidate: Path | None = None

    if source_file:
        candidate = source_file
    else:
        for ext in (".md", ".docx", ".doc", ".txt"):
            p = project.root / f"source{ext}"
            if p.exists():
                candidate = p
                break

        if candidate is None and project.outline_path.exists():
            candidate = project.outline_path

    if candidate is None or not candidate.exists():
        raise ExpansionInputError(
            f"No source document found in {project.root}. "
            "Upload a source.md / source.docx file, or pass a path."
        )

    text = _read_document(candidate)

    if is_default_scaffold(text):
        raise ExpansionInputError(
            f"{candidate.name} is still the default scaffold from `scribe init`. "
            "Replace it with a real draft to expand."
        )

    if not looks_like_substantive_draft(text):
        words = len(text.split())
        raise ExpansionInputError(
            f"{candidate.name} does not look like a substantive draft "
            f"({words} words; expected >=1,500 with headings and prose). "
            "Expansion needs an existing argument to deepen."
        )

    sections = split_into_sections(text)
    if len(sections) < 2:
        raise ExpansionInputError(
            "Document has fewer than 2 heading-delimited sections; cannot "
            "expand section by section. Add Markdown headings (# Section) and retry."
        )

    return text, sections


def _resolve_target_words(
    current_words: int,
    target_words: int | None,
    multiplier: float | None,
) -> tuple[int, float]:
    """Work out the final target word count and multiplier."""
    if target_words and target_words > current_words:
        m = target_words / max(current_words, 1)
        return target_words, m
    if multiplier and multiplier > 1.0:
        return int(current_words * multiplier), multiplier
    # Default: 2x expansion
    return current_words * 2, 2.0


async def run_expansion_pipeline(
    project: Project,
    config: ScribeConfig,
    target_words: int | None = None,
    multiplier: float | None = None,
    source_file: Path | None = None,
    stream_callback: StreamCallback | None = None,
    reuse_plan: bool = True,
    run_smoother: bool = True,
) -> dict:
    """Run the full expansion pipeline. Returns a summary dict."""
    project.ensure_dirs()
    pipeline_start = time.time()

    # Stage 1: parse
    logger.info("Expansion stage 1/4: parsing document")
    document_text, sections = load_source_document_for_expansion(project, source_file)
    current_words = sum(s.word_count for s in sections if s.id != "preamble")

    # Archive the source for auditability
    archive = project.scribe_dir / "source_snapshot.md"
    archive.write_text(document_text, encoding="utf-8")

    # Resolve targets
    final_target, final_multiplier = _resolve_target_words(
        current_words, target_words, multiplier,
    )

    # Reference materials (optional deeper context for the expander)
    ref_texts: dict[str, str] = {}
    ref_paths: list[str] = []
    if project.refs_dir.exists() and any(project.refs_dir.iterdir()):
        ref_texts = extract_all_refs(
            project.refs_dir, cache_dir=project.extracted_dir,
        )
        ref_paths = list(ref_texts.keys())

    # Stage 2: plan (reuse cached plan if available and parameters match)
    if reuse_plan and project.expansion_plan_path.exists():
        logger.info(
            "Expansion stage 2/4: reusing cached plan at %s",
            project.expansion_plan_path,
        )
        plan = DocumentExpansion.load(project.expansion_plan_path)
        # If the cached plan targets a different word count, regenerate.
        if abs(plan.target_total_words - final_target) > final_target * 0.05:
            logger.info(
                "Cached plan targets %d words, new target is %d; replanning.",
                plan.target_total_words, final_target,
            )
            plan = await run_expansion_planner(
                project=project,
                config=config,
                sections=sections,
                document_text=document_text,
                target_words=final_target,
                multiplier=final_multiplier,
                ref_texts=ref_texts,
                stream_callback=stream_callback,
            )
    else:
        logger.info("Expansion stage 2/4: planning expansion for %d sections",
                    len(sections))
        plan = await run_expansion_planner(
            project=project,
            config=config,
            sections=sections,
            document_text=document_text,
            target_words=final_target,
            multiplier=final_multiplier,
            ref_texts=ref_texts,
            stream_callback=stream_callback,
        )

    # Stage 3: expand
    logger.info(
        "Expansion stage 3/4: expanding sections (parallelism=%d)",
        config.parallelism,
    )
    exec_result: ExpansionRunResult = await run_expander(
        project=project,
        config=config,
        expansion_plan=plan,
        sections=sections,
        ref_paths_available=ref_paths,
        stream_callback=stream_callback,
    )

    # Pull out the title from the first H1, if any
    title = ""
    for section in sections:
        if section.level == 1:
            title = section.title
            break

    assembled = assemble_expanded_document(exec_result.results)

    # Stage 4: smooth transitions
    if run_smoother:
        logger.info("Expansion stage 4/4: smoothing transitions")
        # Reuse the revision stitcher -- its job (smooth joins + terminology)
        # is identical across revise and expand.
        from scribe.stages.revision_stitcher import run_revision_stitcher

        # Redirect output to the expanded path, not revised.md
        original_revised = project.revised_path
        original_expanded = project.expanded_path

        # run_revision_stitcher writes to project.revised_path; we then move it.
        stitched_path = await run_revision_stitcher(
            project=project,
            config=config,
            revised_document=assembled,
            stream_callback=stream_callback,
        )
        # Move the output from revised.md to expanded.md
        if stitched_path.exists() and stitched_path != original_expanded:
            text = stitched_path.read_text(encoding="utf-8")
            original_expanded.write_text(text, encoding="utf-8")
        final_path = original_expanded
    else:
        project.expanded_path.write_text(assembled, encoding="utf-8")
        final_path = project.expanded_path

    # Auto-export a Word version alongside the markdown.
    from scribe.export import try_export_sibling
    try_export_sibling(final_path)

    duration = time.time() - pipeline_start
    expanded_words = len(final_path.read_text(encoding="utf-8").split())

    summary = {
        "title": title or plan.source_title,
        "source_path": str(archive),
        "expanded_path": str(final_path),
        "plan_path": str(project.expansion_plan_path),
        "plan_summary_path": str(project.expansion_plan_summary_path),
        "original_words": current_words,
        "target_words": final_target,
        "expanded_words": expanded_words,
        "multiplier": final_multiplier,
        "achieved_multiplier": expanded_words / max(current_words, 1),
        "sections_total": len(sections),
        "sections_expanded": sum(
            1 for r in exec_result.results
            if r.expanded_words > r.original_words + 50
        ),
        "sections_failed": len(exec_result.failed_section_ids),
        "duration_s": duration,
        "overall_strategy": plan.overall_strategy,
    }

    logger.info(
        "Expansion complete in %.1fs: %d -> %d words (target %d, %.2fx)",
        duration, summary["original_words"], summary["expanded_words"],
        summary["target_words"], summary["achieved_multiplier"],
    )
    return summary


def save_source_upload(
    project: Project,
    uploaded_file_path: Path,
    original_filename: str,
) -> Path:
    """Store an uploaded source document at the canonical location.

    Shared with the revision pipeline -- the underlying file is the same.
    """
    project.ensure_dirs()
    ext = Path(original_filename).suffix.lower() or ".md"
    dest = project.root / f"source{ext}"

    for e in (".md", ".txt", ".docx", ".doc"):
        p = project.root / f"source{e}"
        if p.exists() and p != dest:
            p.unlink()

    shutil.copy2(uploaded_file_path, dest)
    return dest
