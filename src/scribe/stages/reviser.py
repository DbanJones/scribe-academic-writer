"""Reviser stage -- per-section revision of an existing draft.

The reviser takes the auditor's findings and revises each section in place,
preserving all citations, quantitative data, figures, tables, and claims.
This is NOT generation; it is targeted editing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scribe.config import ScribeConfig
from scribe.models import DocumentAudit, SectionAudit, SectionRevision
from scribe.parsers.sections import DocumentSection
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke_parallel

logger = logging.getLogger(__name__)


def _build_reviser_prompt(
    section: DocumentSection,
    section_audit: SectionAudit | None,
    overall_context: str,
    style_text: str,
    academic_rules_preamble: str,
) -> tuple[str, str]:
    """Build the reviser prompt for one section."""

    preservation_block = (
        "HARD CONSTRAINTS (non-negotiable):\n"
        "1. PRESERVE every citation exactly. Do not add, remove, or alter "
        "citation strings. Citations currently in the section:\n"
        + (
            "\n".join(f"   - {c}" for c in section.citations)
            if section.citations
            else "   (none)"
        )
        + "\n"
        "2. PRESERVE every numeric claim, percentage, range, date, and unit. "
        "Do not round, approximate, or alter quantitative data.\n"
        "3. PRESERVE every figure and table reference ('Figure 1', 'Table 2'). "
        "Figures/tables referenced in this section:\n"
        + (
            "\n".join(f"   - {f}" for f in section.figures)
            if section.figures
            else "   (none)"
        )
        + "\n"
        "4. PRESERVE the author's original framework, terminology, and "
        "argument structure. Do not introduce new claims, new sources, or "
        "new analytical frames.\n"
        "5. PRESERVE the section heading exactly as provided. Do not change "
        "heading text or level.\n"
        "6. DO NOT add meta-commentary ('In this section we will...', "
        "'As demonstrated above...'). Cut such phrasing where it exists.\n"
        "7. DO NOT add new sources or citations. If a claim currently lacks "
        "a citation, leave it as-is; do not invent one.\n"
        "8. DO NOT hedge claims that were stated definitively, and do not "
        "strengthen claims that were hedged. Preserve the author's stance.\n"
    )

    issues_block = ""
    if section_audit and section_audit.issues:
        lines = ["TARGETED ISSUES TO FIX (from the audit):\n"]
        for i, issue in enumerate(section_audit.issues, 1):
            lines.append(f"{i}. [{issue.severity}] {issue.category}: {issue.issue}")
            if issue.original:
                lines.append(f"   Original: \"{issue.original}\"")
            if issue.suggestion:
                lines.append(f"   Fix: {issue.suggestion}")
        issues_block = "\n".join(lines) + "\n"

    strengths_block = ""
    if section_audit and section_audit.strengths:
        strengths_block = (
            "STRENGTHS TO PRESERVE (the audit flagged these as working well):\n"
            + "\n".join(f"- {s}" for s in section_audit.strengths)
            + "\n"
        )

    structural_role = ""
    if section_audit and section_audit.structural_role:
        structural_role = (
            f"STRUCTURAL ROLE: This section's role in the document is "
            f"**{section_audit.structural_role}** (Allwood's six-element framework).\n"
            "Write in a way that serves this role.\n"
        )

    system = (
        "You are revising one section of an existing academic draft against "
        "the academic writing rules. You are an editor, not a co-author. "
        "The author's claims, framework, citations, and data are inviolable. "
        "Your sole job is to improve HOW things are said.\n\n"
        f"{academic_rules_preamble}\n"
        "STYLE GUIDE (apply throughout):\n"
        f"{style_text}\n"
    )

    user = f"""\
Revise the section below.

{preservation_block}
{issues_block}{strengths_block}{structural_role}

{overall_context}

---

## SECTION TO REVISE

Heading: {("#" * section.level)} {section.title}

{section.body}

---

Output the revised section as clean markdown. Start with the heading line
(preserved exactly as shown above), then the revised body. No commentary,
no explanation, no meta-text about what you changed. Just the revised section.
"""
    return system, user


@dataclass
class RevisionResult:
    section_id: str
    section_title: str
    original_words: int
    revised_text: str
    revised_words: int
    is_error: bool = False
    error: str = ""
    duration_s: float = 0.0


async def run_reviser(
    project: Project,
    config: ScribeConfig,
    audit: DocumentAudit,
    sections: list[DocumentSection],
    overall_context: str = "",
    stream_callback: StreamCallback | None = None,
) -> list[RevisionResult]:
    """Revise every section in parallel. Returns results in section order."""
    from scribe.stages.prompts import ACADEMIC_RULES_PREAMBLE

    project.ensure_dirs()
    style_text = project.load_style()

    # Index audits by section id
    audit_by_id: dict[str, SectionAudit] = {s.section_id: s for s in audit.sections}

    # Skip preamble + trivial sections from revision (keep them verbatim)
    to_revise: list[DocumentSection] = []
    untouched: list[DocumentSection] = []
    for s in sections:
        if s.id == "preamble" or s.word_count < 30:
            untouched.append(s)
        else:
            to_revise.append(s)

    logger.info(
        "Revising %d sections in parallel (%d untouched)",
        len(to_revise), len(untouched),
    )

    tasks: list[dict[str, Any]] = []
    for section in to_revise:
        system, user = _build_reviser_prompt(
            section=section,
            section_audit=audit_by_id.get(section.id),
            overall_context=overall_context,
            style_text=style_text,
            academic_rules_preamble=ACADEMIC_RULES_PREAMBLE,
        )
        tasks.append({
            "prompt": user,
            "model": config.executor_model_id,
            "system_prompt": system,
            "cwd": project.root,
            "allowed_tools": [],
            "max_turns": 1,
            "callback_id": section.id,
        })

    start = time.time()
    responses = await invoke_parallel(
        tasks,
        max_concurrent=config.parallelism,
        stream_callback=stream_callback,
    )

    results: list[RevisionResult] = []

    # Untouched sections pass through verbatim
    untouched_by_id = {s.id: s for s in untouched}

    # Interleave results back in original order
    revise_by_id: dict[str, tuple[DocumentSection, SDKResponse]] = {}
    for section, response in zip(to_revise, responses):
        revise_by_id[section.id] = (section, response)

    for section in sections:
        if section.id in untouched_by_id:
            results.append(RevisionResult(
                section_id=section.id,
                section_title=section.title,
                original_words=section.word_count,
                revised_text=section.text,
                revised_words=section.word_count,
                is_error=False,
            ))
            continue

        section, response = revise_by_id[section.id]
        elapsed = time.time() - start

        if response.is_error:
            # Fall back to the original
            results.append(RevisionResult(
                section_id=section.id,
                section_title=section.title,
                original_words=section.word_count,
                revised_text=section.text,
                revised_words=section.word_count,
                is_error=True,
                error=response.text[:300],
                duration_s=elapsed,
            ))
            logger.error("Section %s failed, keeping original: %s",
                         section.id, response.text[:150])
            continue

        revised_text = response.text.strip()

        # Sanity check: ensure the heading is preserved
        expected_heading = f"{'#' * section.level} {section.title}"
        if not revised_text.startswith(expected_heading):
            # Prepend the heading if missing
            revised_text = f"{expected_heading}\n\n{revised_text}"

        results.append(RevisionResult(
            section_id=section.id,
            section_title=section.title,
            original_words=section.word_count,
            revised_text=revised_text,
            revised_words=len(revised_text.split()),
            is_error=False,
            duration_s=response.duration_ms / 1000,
        ))

        # Save per-section revision
        revision_path = project.revisions_dir / section.filename
        revision_path.write_text(revised_text, encoding="utf-8")

    return results


def assemble_revised_document(
    results: list[RevisionResult],
    title: str = "",
) -> str:
    """Assemble revision results into a single markdown document."""
    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")
    for r in results:
        parts.append(r.revised_text.strip())
    return "\n\n".join(parts) + "\n"
