"""Expander stage -- per-section expansion using Sonnet in parallel.

Each section is expanded independently using its SectionExpansion plan as
targeted guidance, with hard constraints on preservation. References available
in refs/ are provided via the extracted cache so the expander can draw on
additional source material without inventing citations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribe.config import ScribeConfig
from scribe.models import (
    DocumentExpansion,
    SectionExpansion,
    SectionExpansionResult,
)
from scribe.parsers.sections import DocumentSection
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke_parallel

logger = logging.getLogger(__name__)


def _build_expander_prompt(
    section: DocumentSection,
    section_plan: SectionExpansion | None,
    overall_strategy: str,
    preserved_framework: list[str],
    style_text: str,
    academic_rules_preamble: str,
    ref_paths_available: list[str],
) -> tuple[str, str]:
    """Build the Sonnet prompt for one section's expansion."""

    preservation_block = (
        "HARD CONSTRAINTS (non-negotiable):\n"
        "1. PRESERVE every sentence's underlying claim. You may split, reorder,"
        " and elaborate, but you must not contradict, weaken, or drop a claim"
        " already made.\n"
        "2. PRESERVE every citation exactly as it appears. Do not alter any"
        " citation string. Current citations in this section:\n"
        + (
            "\n".join(f"   - {c}" for c in section.citations)
            if section.citations
            else "   (none)"
        )
        + "\n"
        "3. PRESERVE every number, percentage, range, date, and unit verbatim."
        " Do not round, approximate, or shift any quantitative claim.\n"
        "4. PRESERVE every figure and table reference. Figures/tables in this"
        " section:\n"
        + (
            "\n".join(f"   - {f}" for f in section.figures)
            if section.figures
            else "   (none)"
        )
        + "\n"
        "5. PRESERVE the section heading exactly as provided.\n"
        "6. DO NOT invent citations. You may add a citation ONLY from the"
        " reference materials available in this project (listed below).\n"
        "7. DO NOT introduce new substantive claims that the author has not"
        " made. Expansion unpacks existing arguments; it does not add new"
        " ones.\n"
        "8. DO NOT add meta-commentary ('In this section we will...', 'As we"
        " shall see...').\n"
    )

    if preserved_framework:
        preservation_block += (
            "\nAUTHOR'S FRAMEWORK (must survive expansion):\n"
            + "\n".join(f"   - {term}" for term in preserved_framework)
            + "\n"
        )

    if section_plan and section_plan.preserve_notes:
        preservation_block += (
            "\nSECTION-SPECIFIC ITEMS TO PRESERVE VERBATIM:\n"
            + "\n".join(f"   - {note}" for note in section_plan.preserve_notes)
            + "\n"
        )

    refs_block = ""
    if ref_paths_available:
        refs_block = (
            "REFERENCE MATERIAL AVAILABLE (you may Read these files for"
            " deeper context; cite sparingly and only when material is"
            " directly relevant):\n"
            + "\n".join(f"   - {p}" for p in ref_paths_available)
            + "\n"
        )

    targets_block = ""
    if section_plan and section_plan.targets:
        lines = [
            "EXPANSION TARGETS (from the planner; use these to deepen specific"
            " passages rather than scaling uniformly):\n"
        ]
        for i, t in enumerate(section_plan.targets, 1):
            lines.append(f"{i}. [{t.category}] ({t.suggested_words} words): {t.opportunity}")
            if t.current_text:
                lines.append(f"   Current passage: \"{t.current_text[:180]}\"")
        targets_block = "\n".join(lines) + "\n"

    strategy_block = ""
    if section_plan and section_plan.expansion_strategy:
        strategy_block = (
            f"EXPANSION STRATEGY FOR THIS SECTION:\n   "
            f"{section_plan.expansion_strategy}\n"
        )

    role_block = ""
    if section_plan and section_plan.structural_role:
        role_block = (
            f"STRUCTURAL ROLE: This section functions as **"
            f"{section_plan.structural_role}** in Allwood's six-element"
            " framework. Keep that role as you deepen it.\n"
        )

    system = (
        "You are expanding one section of an existing academic draft. The"
        " author has a coherent argument and you must serve it. Your job is"
        " to unpack compressed passages, spell out mechanisms, surface"
        " evidence, address implied counter-arguments, and add appropriate"
        " definitions or context. You do not add new substantive claims.\n\n"
        f"{academic_rules_preamble}\n"
        "STYLE GUIDE (apply throughout):\n"
        f"{style_text}\n"
    )

    target_words = (section_plan.target_words if section_plan else 0) or int(
        section.word_count * 1.8
    )
    current_words = section.word_count

    user = f"""\
Expand the section below from approximately {current_words:,} words to
approximately {target_words:,} words.

{preservation_block}
{refs_block}{role_block}{strategy_block}{targets_block}
OVERALL DOCUMENT STRATEGY (for coherence):
{overall_strategy or "(not provided)"}

---

## SECTION TO EXPAND

Heading: {("#" * section.level)} {section.title}

{section.body}

---

Output the expanded section as clean markdown. Start with the heading line
(preserved exactly), then the expanded body. No commentary, no explanation
of changes, no meta-text. Just the expanded section.
"""
    return system, user


@dataclass
class ExpansionRunResult:
    """Aggregate result of the expander stage."""
    results: list[SectionExpansionResult]
    failed_section_ids: list[str]


async def run_expander(
    project: Project,
    config: ScribeConfig,
    expansion_plan: DocumentExpansion,
    sections: list[DocumentSection],
    ref_paths_available: list[str] | None = None,
    stream_callback: StreamCallback | None = None,
) -> ExpansionRunResult:
    """Expand every section in parallel and write per-section files."""
    from scribe.stages.prompts import ACADEMIC_RULES_PREAMBLE

    project.ensure_dirs()
    style_text = project.load_style()
    ref_paths_available = ref_paths_available or []

    plan_by_id: dict[str, SectionExpansion] = {
        s.section_id: s for s in expansion_plan.sections
    }

    # Skip sections that should pass through untouched (preamble, very short,
    # or sections the plan explicitly kept at the same word count).
    to_expand: list[DocumentSection] = []
    untouched: list[DocumentSection] = []
    for s in sections:
        plan_s = plan_by_id.get(s.id)
        if s.id == "preamble" or s.word_count < 30:
            untouched.append(s)
        elif plan_s and plan_s.target_words <= plan_s.current_words + 20:
            untouched.append(s)  # planner chose not to expand this one
        else:
            to_expand.append(s)

    logger.info(
        "Expanding %d sections in parallel (%d untouched)",
        len(to_expand), len(untouched),
    )

    tasks: list[dict[str, Any]] = []
    for section in to_expand:
        section_plan = plan_by_id.get(section.id)
        system, user = _build_expander_prompt(
            section=section,
            section_plan=section_plan,
            overall_strategy=expansion_plan.overall_strategy,
            preserved_framework=expansion_plan.preserved_framework,
            style_text=style_text,
            academic_rules_preamble=ACADEMIC_RULES_PREAMBLE,
            ref_paths_available=ref_paths_available,
        )
        tasks.append({
            "prompt": user,
            "model": config.executor_model_id,
            "system_prompt": system,
            "cwd": project.root,
            "allowed_tools": ["Read", "Grep", "Glob"] if ref_paths_available else [],
            "max_turns": 6 if ref_paths_available else 1,
            "callback_id": section.id,
        })

    start = time.time()
    responses = await invoke_parallel(
        tasks,
        max_concurrent=config.parallelism,
        stream_callback=stream_callback,
    )

    results: list[SectionExpansionResult] = []
    failed: list[str] = []

    untouched_ids = {s.id for s in untouched}
    expand_by_id: dict[str, tuple[DocumentSection, SDKResponse]] = {}
    for section, response in zip(to_expand, responses):
        expand_by_id[section.id] = (section, response)

    for section in sections:
        if section.id in untouched_ids:
            results.append(SectionExpansionResult(
                section_id=section.id,
                section_title=section.title,
                original_words=section.word_count,
                expanded_words=section.word_count,
                target_words=section.word_count,
                expanded_text=section.text,
            ))
            continue

        section_match, response = expand_by_id[section.id]
        elapsed = time.time() - start
        section_plan = plan_by_id.get(section.id)
        target_words = section_plan.target_words if section_plan else 0

        if response.is_error:
            # Keep the original section on failure.
            logger.error(
                "Section %s expansion failed, keeping original: %s",
                section.id, response.text[:150],
            )
            failed.append(section.id)
            results.append(SectionExpansionResult(
                section_id=section.id,
                section_title=section.title,
                original_words=section.word_count,
                expanded_words=section.word_count,
                target_words=target_words,
                expanded_text=section.text,
                preserved_citations=section.citations,
            ))
            continue

        expanded_text = response.text.strip()
        expected_heading = f"{'#' * section.level} {section.title}"
        if not expanded_text.startswith(expected_heading):
            expanded_text = f"{expected_heading}\n\n{expanded_text}"

        results.append(SectionExpansionResult(
            section_id=section.id,
            section_title=section.title,
            original_words=section.word_count,
            expanded_words=len(expanded_text.split()),
            target_words=target_words,
            expanded_text=expanded_text,
            preserved_citations=section.citations,
        ))

        out_path = project.expansions_dir / section.filename
        out_path.write_text(expanded_text, encoding="utf-8")

    return ExpansionRunResult(results=results, failed_section_ids=failed)


def assemble_expanded_document(
    results: list[SectionExpansionResult],
    title: str = "",
) -> str:
    """Assemble expansion results into a single markdown document."""
    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")
    for r in results:
        parts.append(r.expanded_text.strip())
    return "\n\n".join(parts) + "\n"
