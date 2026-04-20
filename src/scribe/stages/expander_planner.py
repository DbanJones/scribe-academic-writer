"""Expansion-planner stage.

Opus reads a substantive draft and any additional reference material, then
produces a per-section expansion plan that tells the downstream expander:

- how much to expand each section (word budget)
- where each section is compressed (specific targets)
- what must be preserved verbatim (claims, framework, citations, data)
- the overall strategy for deepening the document

This is structurally similar to the audit, but the goal is different:
the audit flags what's wrong, the expansion plan flags what's missing depth.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import DocumentExpansion
from scribe.parsers.sections import DocumentSection
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke

logger = logging.getLogger(__name__)


EXPANSION_JSON_SCHEMA = """\
{
  "type": "object",
  "required": ["source_title", "current_total_words", "target_total_words", "multiplier", "overall_strategy", "preserved_framework", "sections"],
  "properties": {
    "source_title": {"type": "string"},
    "current_total_words": {"type": "integer"},
    "target_total_words": {"type": "integer"},
    "multiplier": {"type": "number"},
    "overall_strategy": {
      "type": "string",
      "description": "2-4 sentences on how the expansion deepens the paper without drifting from the author's thesis."
    },
    "preserved_framework": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Key terms, classifications, or constructs the author coined that must survive unchanged."
    },
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["section_id", "section_title", "current_words", "target_words", "structural_role", "preserve_notes", "targets", "expansion_strategy"],
        "properties": {
          "section_id": {"type": "string"},
          "section_title": {"type": "string"},
          "structural_role": {"type": "string", "description": "Allwood element: Context, Literature, Proposal, Test, Results, Discussion, or Other"},
          "current_words": {"type": "integer"},
          "target_words": {"type": "integer"},
          "preserve_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Items that must survive verbatim: key claims, data points, framework terms."
          },
          "targets": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["category", "opportunity", "suggested_words"],
              "properties": {
                "category": {
                  "type": "string",
                  "description": "underdeveloped_claim, missing_example, thin_evidence, citation_needs_unpacking, counterargument_missing, methodology_brief, mechanism_compressed, context_missing, scope_assumption, definition_needed, or transition_weak"
                },
                "current_text": {"type": "string", "description": "Short quote (<30 words) of the compressed passage."},
                "opportunity": {"type": "string", "description": "What depth could be added, in 1-2 sentences."},
                "suggested_words": {"type": "integer", "description": "Rough word budget for this specific expansion."}
              }
            }
          },
          "expansion_strategy": {
            "type": "string",
            "description": "1-2 sentences summarising the shape of this section's expansion."
          }
        }
      }
    }
  }
}"""


def _build_expansion_prompt(
    document_text: str,
    sections: list[DocumentSection],
    style_text: str,
    multiplier: float,
    current_total: int,
    target_total: int,
    ref_texts: dict[str, str],
) -> tuple[str, str]:
    """Build the Opus prompt that produces the expansion plan."""
    system = (
        "You are an academic editor planning the expansion of a substantive "
        "draft into a longer, deeper version. The author already has a "
        "coherent argument. Your job is to identify where the current prose "
        "is compressed, not where it is wrong.\n\n"
        "Guiding principles:\n"
        "- Depth, not breadth. Expansion unpacks existing claims, not new ones.\n"
        "- Preserve voice. The author's terminology, framework, and stance are "
        "inviolable.\n"
        "- Every citation, number, and figure must be carried forward.\n"
        "- No invented sources. Claims that currently lack citations must "
        "either remain as authorial assertions or gain citations only from "
        "the reference material below.\n"
        "- Expansion opportunities are specific: a compressed mechanism, a "
        "claim stated without its evidence, a counterargument implied but not "
        "addressed, a definition assumed rather than given.\n\n"
        "STYLE GUIDE (the expansion must conform to this):\n"
        f"{style_text}\n"
    )

    sections_block = "\n\n".join(
        f"--- SECTION {s.id} [h{s.level}] {s.title} "
        f"({s.word_count} words, {len(s.citations)} citations) ---\n"
        f"{s.text}"
        for s in sections
    )

    refs_block = ""
    if ref_texts:
        refs_parts = [f"### {path}\n\n{text}" for path, text in ref_texts.items()]
        refs_block = (
            "\n\n## REFERENCE MATERIALS (available to the expander)\n\n"
            + "\n\n---\n\n".join(refs_parts)
        )

    user = f"""\
Plan an expansion of the document below from approximately {current_total:,}
words to approximately {target_total:,} words (multiplier: {multiplier:.1f}x).

For each section, identify:

1. **Current word count** (given above; echo it).
2. **Target word count** for this section. Weight the budget toward sections
   that are most compressed relative to their argumentative load; leave the
   references list and short framing sections largely untouched.
3. **Structural role** in Allwood's six-element framework.
4. **Preserve notes**: specific claims, data points, framework terms, and
   citations that must survive expansion verbatim. Be concrete.
5. **Expansion targets**: 2-6 per section. Each target must name:
   - a category (underdeveloped_claim, missing_example, thin_evidence,
     citation_needs_unpacking, counterargument_missing, methodology_brief,
     mechanism_compressed, context_missing, scope_assumption,
     definition_needed, transition_weak);
   - a short quote of the compressed passage (current_text, <30 words);
   - the opportunity in 1-2 sentences (what depth could be added);
   - a suggested word budget for this target.
6. **Expansion strategy**: 1-2 sentences on the shape of this section's
   expansion.

For the document as a whole, also capture:

- A concise **overall_strategy**: how the expansion deepens the paper
  without drifting from the author's thesis.
- **Preserved framework**: key terms, classifications, or analytical
  constructs the author coined that must survive unchanged.

Do NOT propose new sections, new claims, or new citations. The expander will
preserve all existing ones and may add citations only from the reference
materials below. Content already in the document is inviolable.

---

## DOCUMENT TO EXPAND

{sections_block}
{refs_block}

---

Output ONLY valid JSON matching this schema. No markdown fences, no commentary.

{EXPANSION_JSON_SCHEMA}
"""
    return system, user


async def run_expansion_planner(
    project: Project,
    config: ScribeConfig,
    sections: list[DocumentSection],
    document_text: str,
    target_words: int,
    multiplier: float,
    ref_texts: dict[str, str] | None = None,
    stream_callback: StreamCallback | None = None,
) -> DocumentExpansion:
    """Produce the DocumentExpansion plan."""
    style_text = project.load_style()
    current_total = sum(s.word_count for s in sections if s.id != "preamble")

    system, user = _build_expansion_prompt(
        document_text, sections, style_text,
        multiplier, current_total, target_words,
        ref_texts or {},
    )

    try:
        response = await invoke(
            prompt=user,
            model=(
                config.reviewer_model_id
                if hasattr(config, "reviewer_model_id")
                else config.planner_model_id
            ),
            system_prompt=system,
            cwd=project.root,
            allowed_tools=[],
            max_turns=1,
            stream_callback=stream_callback,
            callback_id="expansion_planner",
        )
    except Exception as e:  # noqa: BLE001 -- degrade gracefully
        logger.error(
            "Expansion planner crashed with %s: %s. Falling back to flat multiplier.",
            type(e).__name__, str(e)[:300],
        )
        return _flat_fallback_plan(sections, current_total, target_words, multiplier)

    if response.is_error:
        logger.error(
            "Expansion planner returned error: %s. Falling back to flat multiplier.",
            response.text[:300],
        )
        return _flat_fallback_plan(sections, current_total, target_words, multiplier)

    plan = _parse_expansion_response(response)

    # Fill totals if the model omitted them
    if not plan.current_total_words:
        plan.current_total_words = current_total
    if not plan.target_total_words:
        plan.target_total_words = target_words
    if not plan.multiplier:
        plan.multiplier = multiplier

    project.ensure_dirs()
    plan.save(project.expansion_plan_path)
    project.expansion_plan_summary_path.write_text(
        render_expansion_plan(plan), encoding="utf-8",
    )

    logger.info(
        "Expansion plan: %d sections, %d -> %d words (%.2fx)",
        len(plan.sections), plan.current_total_words, plan.target_total_words, plan.multiplier,
    )
    return plan


def _parse_expansion_response(response: SDKResponse) -> DocumentExpansion:
    text = response.text.strip()

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    if not text.startswith("{"):
        start = text.find("{")
        if start >= 0:
            text = text[start:]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Expansion planner returned invalid JSON: {e}\n\n"
                f"Response text:\n{response.text[:500]}"
            ) from e

    return DocumentExpansion.model_validate(data)


def _flat_fallback_plan(
    sections: list[DocumentSection],
    current_total: int,
    target_total: int,
    multiplier: float,
) -> DocumentExpansion:
    """Build a very plain expansion plan when the planner call fails.

    Each section is scaled by the flat multiplier with no targeted
    opportunities. Better than crashing; the expander will still deepen
    each section but without targeted guidance.
    """
    from scribe.models import SectionExpansion

    plan_sections = []
    for s in sections:
        if s.id == "preamble" or s.word_count < 30:
            plan_sections.append(SectionExpansion(
                section_id=s.id,
                section_title=s.title,
                current_words=s.word_count,
                target_words=s.word_count,  # untouched
            ))
        else:
            plan_sections.append(SectionExpansion(
                section_id=s.id,
                section_title=s.title,
                current_words=s.word_count,
                target_words=int(s.word_count * multiplier),
                expansion_strategy="Scale uniformly; no targeted opportunities identified.",
            ))

    return DocumentExpansion(
        source_title="(expansion planner fell back)",
        current_total_words=current_total,
        target_total_words=target_total,
        multiplier=multiplier,
        overall_strategy=(
            "Planner call failed; falling back to a flat multiplier. Each "
            "section is scaled uniformly without targeted expansion guidance."
        ),
        sections=plan_sections,
    )


def render_expansion_plan(plan: DocumentExpansion) -> str:
    """Human-readable markdown summary of the expansion plan."""
    lines = [
        f"# Expansion plan: {plan.source_title or '(untitled)'}",
        "",
        f"**Current:** {plan.current_total_words:,} words  ",
        f"**Target:**  {plan.target_total_words:,} words  ",
        f"**Multiplier:** {plan.multiplier:.2f}x  ",
        "",
        "## Overall strategy",
        "",
        plan.overall_strategy or "_(none)_",
        "",
    ]

    if plan.preserved_framework:
        lines.extend(["## Preserved framework", ""])
        for term in plan.preserved_framework:
            lines.append(f"- {term}")
        lines.append("")

    lines.extend(["## Sections", ""])
    for section in plan.sections:
        growth = ""
        if section.current_words:
            growth = f" ({section.target_words / max(section.current_words, 1):.1f}x)"
        lines.append(
            f"### {section.section_title} "
            f"({section.current_words:,} -> {section.target_words:,}{growth})"
        )
        if section.structural_role:
            lines.append(f"Role: **{section.structural_role}**")
        if section.expansion_strategy:
            lines.append("")
            lines.append(f"_Strategy:_ {section.expansion_strategy}")
        if section.preserve_notes:
            lines.append("")
            lines.append("_Preserve:_")
            for note in section.preserve_notes:
                lines.append(f"- {note}")
        if section.targets:
            lines.append("")
            lines.append("_Expansion targets:_")
            for t in section.targets:
                lines.append(
                    f"- [{t.category}] ({t.suggested_words} words): {t.opportunity}"
                )
                if t.current_text:
                    lines.append(f"  - _Current:_ \"{t.current_text[:120]}\"")
        lines.append("")

    return "\n".join(lines)
