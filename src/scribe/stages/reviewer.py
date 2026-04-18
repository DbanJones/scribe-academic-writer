"""Reviewer stage -- Opus pre-analysis of the document's thesis and structure."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from scribe.config import ScribeConfig
from scribe.models import DocumentReview
from scribe.parsers.refs import extract_all_refs
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke

logger = logging.getLogger(__name__)

REVIEW_JSON_SCHEMA = """\
{
  "type": "object",
  "required": ["problem_statement", "need_for_resolution", "existing_gap", "key_question", "key_themes", "section_mappings"],
  "properties": {
    "problem_statement": {
      "type": "string",
      "description": "The core problem the document addresses, stated in 1-3 sentences"
    },
    "need_for_resolution": {
      "type": "string",
      "description": "Why resolving this problem matters -- the stakes, consequences, or urgency"
    },
    "existing_gap": {
      "type": "string",
      "description": "What gap in knowledge, practice, or policy currently exists that this document fills"
    },
    "key_question": {
      "type": "string",
      "description": "The single central question the document answers, stated as a question"
    },
    "key_themes": {
      "type": "array",
      "items": {"type": "string"},
      "description": "3-7 major themes that run through the document"
    },
    "theme_descriptions": {
      "type": "object",
      "additionalProperties": {"type": "string"},
      "description": "One-sentence description of each theme's role in the argument"
    },
    "section_mappings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "section": {"type": "string", "description": "Section/heading name from the outline"},
          "role": {"type": "string", "description": "This section's role in the overall argument"},
          "themes_addressed": {"type": "array", "items": {"type": "string"}},
          "answers_question_by": {"type": "string", "description": "How this section contributes to answering the key question"}
        },
        "required": ["section", "role", "themes_addressed", "answers_question_by"]
      }
    },
    "narrative_arc": {
      "type": "string",
      "description": "A 2-4 sentence description of how the document should flow from opening to conclusion"
    },
    "tone_guidance": {
      "type": "string",
      "description": "Observations about the appropriate register, voice, and rhetorical stance"
    }
  }
}"""


def _build_review_prompt(
    outline_text: str,
    style_text: str,
    ref_texts: dict[str, str],
) -> tuple[str, str]:
    """Build the document review prompt."""
    system = (
        "You are a senior academic editor and thesis analyst. "
        "Your task is to read an outline, style guide, and reference materials, "
        "then produce a deep structural analysis of the document's thesis. "
        "This analysis will guide all subsequent writing to maintain coherence.\n\n"
        "STYLE GUIDE:\n"
        f"{style_text}\n"
    )

    refs_block = ""
    if ref_texts:
        refs_parts = []
        for path, text in ref_texts.items():
            refs_parts.append(f"### {path}\n\n{text}")
        refs_block = "\n\n---\n\n".join(refs_parts)

    user = f"""\
Read the outline and reference materials below. Produce a structural thesis analysis as JSON.

You must identify:

1. **Problem statement**: What core problem does this document address? State it in 1-3 precise sentences.

2. **Need for resolution**: Why does this problem matter? What are the stakes -- who is affected, what are the consequences of inaction, why is it urgent?

3. **Existing gap**: What gap in knowledge, practice, policy, or understanding currently exists that this document aims to fill? Be specific about what is missing.

4. **Key question**: What is THE central question the document answers? State it as a single, clear question.

5. **Key themes**: Identify 3-7 major themes that run through the document. These are the recurring ideas, concepts, or arguments that bind the sections together.

6. **Theme descriptions**: For each theme, write one sentence explaining its role in the overall argument.

7. **Section mappings**: For each major section in the outline, explain:
   - Its role in the overall argument (e.g., "establishes the problem", "provides empirical evidence", "synthesises competing views")
   - Which themes it addresses
   - How it contributes to answering the key question

8. **Narrative arc**: Describe in 2-4 sentences how the document should flow from opening to conclusion. What is the intellectual journey the reader takes?

9. **Tone guidance**: What register, voice, and rhetorical stance are appropriate? Note any tensions (e.g., need for technical precision vs. accessibility).

---

## OUTLINE

{outline_text}

---

## REFERENCE MATERIALS

{refs_block if refs_block else "(No reference materials provided.)"}

---

Output ONLY valid JSON matching this schema. No markdown fences, no commentary before or after the JSON.

{REVIEW_JSON_SCHEMA}
"""
    return system, user


async def run_reviewer(
    project: Project,
    config: ScribeConfig,
    stream_callback: StreamCallback | None = None,
) -> DocumentReview:
    """Run the document review stage.

    Reads all inputs, sends to Opus for thesis analysis,
    saves the result for use by all downstream stages.
    """
    outline_text = project.load_outline()
    style_text = project.load_style()
    ref_texts = extract_all_refs(project.refs_dir)

    system, user = _build_review_prompt(outline_text, style_text, ref_texts)

    response = await invoke(
        prompt=user,
        model=config.planner_model_id,  # Opus
        system_prompt=system,
        cwd=project.root,
        allowed_tools=[],
        max_turns=1,
        stream_callback=stream_callback,
        callback_id="reviewer",
    )

    review = _parse_review_response(response)

    # Save
    project.ensure_dirs()
    review.save(project.review_path)

    # Save human-readable summary
    summary = render_review_summary(review)
    project.review_summary_path.write_text(summary, encoding="utf-8")

    logger.info(
        "Document review: %d themes, key question: %s",
        len(review.key_themes),
        review.key_question[:80],
    )
    return review


def _parse_review_response(response: SDKResponse) -> DocumentReview:
    """Extract and parse JSON from the review response."""
    text = response.text.strip()

    # Strip markdown fences
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()

    # Find JSON start
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
                f"Reviewer returned invalid JSON: {e}\n\n"
                f"Response text:\n{response.text[:500]}"
            ) from e

    return DocumentReview.model_validate(data)


def render_review_summary(review: DocumentReview) -> str:
    """Render a human-readable markdown summary of the document review."""
    lines = [
        "# Document Thesis Analysis",
        "",
        "## Problem Statement",
        review.problem_statement,
        "",
        "## Need for Resolution",
        review.need_for_resolution,
        "",
        "## Existing Gap",
        review.existing_gap,
        "",
        "## Key Question",
        f"**{review.key_question}**",
        "",
        "## Key Themes",
        "",
    ]

    for theme in review.key_themes:
        desc = review.theme_descriptions.get(theme, "")
        lines.append(f"- **{theme}**: {desc}" if desc else f"- **{theme}**")

    lines.extend(["", "## Narrative Arc", review.narrative_arc, ""])

    if review.section_mappings:
        lines.extend(["## Section Roles", ""])
        lines.append("| Section | Role | Themes | Answers Question By |")
        lines.append("|---------|------|--------|---------------------|")
        for m in review.section_mappings:
            themes = ", ".join(m.themes_addressed)
            lines.append(f"| {m.section} | {m.role} | {themes} | {m.answers_question_by} |")

    if review.tone_guidance:
        lines.extend(["", "## Tone Guidance", review.tone_guidance])

    lines.append("")
    return "\n".join(lines)
