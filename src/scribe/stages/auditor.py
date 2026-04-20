"""Auditor stage -- Opus evaluates a draft against the academic writing rules.

The auditor reads a complete existing draft and produces a structured
DocumentAudit. The reviser then uses this audit as targeted guidance
for each section, rather than rewriting blind.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import DocumentAudit
from scribe.parsers.sections import DocumentSection
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke

logger = logging.getLogger(__name__)


AUDIT_JSON_SCHEMA = """\
{
  "type": "object",
  "required": ["title", "total_words", "section_count", "overall_issues", "sections", "hourglass_assessment", "six_elements_present", "overall_verdict"],
  "properties": {
    "title": {"type": "string"},
    "total_words": {"type": "integer"},
    "section_count": {"type": "integer"},
    "overall_issues": {
      "type": "array",
      "description": "Document-level issues (structure, framing, scope)",
      "items": {
        "type": "object",
        "required": ["category", "severity", "issue"],
        "properties": {
          "category": {"type": "string"},
          "severity": {"type": "string", "enum": ["high", "medium", "low"]},
          "location": {"type": "string"},
          "original": {"type": "string"},
          "issue": {"type": "string"},
          "suggestion": {"type": "string"}
        }
      }
    },
    "overall_strengths": {
      "type": "array",
      "items": {"type": "string"}
    },
    "sections": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["section_id", "section_title", "word_count", "structural_role", "issues", "revision_priority"],
        "properties": {
          "section_id": {"type": "string"},
          "section_title": {"type": "string"},
          "word_count": {"type": "integer"},
          "structural_role": {
            "type": "string",
            "description": "Allwood element: Context, Literature, Proposal, Test, Results, Discussion, or Other"
          },
          "revision_priority": {"type": "string", "enum": ["high", "medium", "low"]},
          "issues": {
            "type": "array",
            "items": {
              "type": "object",
              "required": ["category", "severity", "issue"],
              "properties": {
                "category": {"type": "string"},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "location": {"type": "string"},
                "original": {"type": "string"},
                "issue": {"type": "string"},
                "suggestion": {"type": "string"}
              }
            }
          },
          "strengths": {"type": "array", "items": {"type": "string"}}
        }
      }
    },
    "hourglass_assessment": {
      "type": "string",
      "description": "Do the opening and resolution widths match? Where does the document overpromise or underpromise?"
    },
    "six_elements_present": {
      "type": "object",
      "description": "Each of Allwood's six elements mapped to 'strong', 'present', 'weak', or 'missing'",
      "properties": {
        "Context": {"type": "string"},
        "Literature": {"type": "string"},
        "Proposal": {"type": "string"},
        "Test": {"type": "string"},
        "Results": {"type": "string"},
        "Discussion": {"type": "string"}
      }
    },
    "overall_verdict": {
      "type": "string",
      "description": "2-4 sentences summarising the document's overall condition and the highest-leverage revisions"
    }
  }
}"""


ISSUE_CATEGORIES = (
    "nominalisation", "zombie_noun", "passive_voice", "empty_verb",
    "weasel_word", "unwarranted_certainty", "inflated_vocabulary",
    "clutter_phrase", "redundant_modifier", "empty_intensifier",
    "subject_verb_distance", "left_branching", "expletive_construction",
    "citation_handling", "missing_synthesis", "positioning",
    "old_to_new_flow", "stress_position", "parallel_structure",
    "metadiscourse", "formality", "contraction", "banned_word",
    "hedging_calibration", "structure", "topic_sentence",
    "paragraph_unity", "figure_reference", "definition_missing",
    "precision", "quantification",
)


def _build_audit_prompt(
    document_text: str,
    sections: list[DocumentSection],
    style_text: str,
) -> tuple[str, str]:
    """Build the audit prompt for Opus."""

    system = (
        "You are a senior academic editor trained in the Allwood method "
        "(Cambridge Engineering writing programme). You are auditing an "
        "existing draft against the academic writing rules.\n\n"
        "Your job is NOT to rewrite the document. Your job is to produce a "
        "structured, actionable audit that the reviser will use as targeted "
        "guidance. Be specific. Quote the offending text. Name the category.\n\n"
        "Every paper should contain six elements:\n"
        "1. Context (the world would be better if...)\n"
        "2. Literature (prior work gives insight, but a gap remains)\n"
        "3. Proposal (for the first time, we...)\n"
        "4. Test design (how to verify the proposal)\n"
        "5. Results (what the test showed, without interpretation)\n"
        "6. Discussion (interpretation, gap filled to some extent)\n"
        "Some document types (e.g. review articles, summary documents) "
        "legitimately compress or fold elements. Judge whether the intended "
        "form is coherent, not whether it matches IMRaD rigidly.\n\n"
        "STYLE GUIDE (the document should conform to this):\n"
        f"{style_text}\n"
    )

    sections_block = "\n\n".join(
        f"--- SECTION {s.id} [h{s.level}] {s.title} "
        f"({s.word_count} words, {len(s.citations)} citations) ---\n"
        f"{s.text}"
        for s in sections
    )

    user = f"""\
Audit the draft below. Produce a structured audit as JSON.

For each section, identify:
- Its structural role in Allwood's six-element framework
- Revision priority (high/medium/low) based on how much work it needs
- Specific issues, each with: category, severity, location (e.g. "paragraph 2"), quoted original text, problem description, concrete fix
- Genuine strengths worth preserving

For the document as a whole, assess:
- Does the opening width match the resolution width? (hourglass shape)
- Are Allwood's six elements present in some form?
- What are the highest-leverage revisions?

Issue categories you may use (pick the most specific):
{", ".join(ISSUE_CATEGORIES)}

Be concrete. A useful issue reads:
  category: "zombie_noun"
  location: "Section s4, paragraph 2"
  original: "The implementation of the policy was carried out by the committee"
  issue: "Nominalisation ('implementation') with empty verb ('was carried out') hides the action"
  suggestion: "The committee implemented the policy"

An unhelpful issue reads:
  category: "clarity"
  issue: "Could be clearer"
  suggestion: "Rewrite"

Do NOT propose new content, new claims, or new citations. The reviser will
preserve all citations, quantitative data, figures, tables, and original
claims exactly. Your audit focuses on HOW things are said, not WHAT is said.

---

## DOCUMENT

{sections_block}

---

Output ONLY valid JSON matching this schema. No markdown fences, no commentary.

{AUDIT_JSON_SCHEMA}
"""
    return system, user


async def run_auditor(
    project: Project,
    config: ScribeConfig,
    sections: list[DocumentSection],
    document_text: str,
    stream_callback: StreamCallback | None = None,
) -> DocumentAudit:
    """Run the auditor stage. Returns the structured DocumentAudit."""
    style_text = project.load_style()
    system, user = _build_audit_prompt(document_text, sections, style_text)

    try:
        response = await invoke(
            prompt=user,
            model=config.reviewer_model_id if hasattr(config, 'reviewer_model_id') else config.planner_model_id,
            system_prompt=system,
            cwd=project.root,
            allowed_tools=[],
            max_turns=1,
            stream_callback=stream_callback,
            callback_id="auditor",
        )
    except Exception as e:  # noqa: BLE001 -- degrade to empty audit
        logger.error(
            "Auditor crashed with %s: %s. Revising with empty audit.",
            type(e).__name__, str(e)[:300],
        )
        from scribe.models import DocumentAudit as _DocumentAudit
        return _DocumentAudit(
            title="(audit failed)",
            total_words=sum(s.word_count for s in sections),
            section_count=len(sections),
            overall_verdict=f"Audit could not be produced: {e}",
        )

    if response.is_error:
        logger.error(
            "Auditor returned error: %s. Revising with empty audit.",
            response.text[:300],
        )
        from scribe.models import DocumentAudit as _DocumentAudit
        return _DocumentAudit(
            title="(audit errored)",
            total_words=sum(s.word_count for s in sections),
            section_count=len(sections),
            overall_verdict=f"Audit errored: {response.text[:200]}",
        )

    audit = _parse_audit_response(response)

    # Fill in defaults the model may have omitted
    if not audit.total_words:
        audit.total_words = sum(s.word_count for s in sections)
    if not audit.section_count:
        audit.section_count = len(sections)

    project.ensure_dirs()
    audit.save(project.audit_path)
    project.audit_summary_path.write_text(
        render_audit_summary(audit), encoding="utf-8"
    )

    logger.info(
        "Audit complete: %d sections, %d overall issues, verdict: %s",
        len(audit.sections), len(audit.overall_issues),
        audit.overall_verdict[:80],
    )
    return audit


def _parse_audit_response(response: SDKResponse) -> DocumentAudit:
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
                f"Auditor returned invalid JSON: {e}\n\n"
                f"Response text:\n{response.text[:500]}"
            ) from e

    return DocumentAudit.model_validate(data)


def render_audit_summary(audit: DocumentAudit) -> str:
    """Human-readable markdown summary."""
    lines = [
        f"# Audit: {audit.title or '(untitled)'}",
        "",
        f"**Total words:** {audit.total_words:,}  ",
        f"**Sections:** {audit.section_count}  ",
        "",
        "## Overall verdict",
        "",
        audit.overall_verdict or "_(none)_",
        "",
        "## Hourglass assessment",
        "",
        audit.hourglass_assessment or "_(none)_",
        "",
        "## Six elements present",
        "",
    ]
    for element, status in audit.six_elements_present.items():
        lines.append(f"- **{element}**: {status}")

    if audit.overall_strengths:
        lines.extend(["", "## Overall strengths", ""])
        for s in audit.overall_strengths:
            lines.append(f"- {s}")

    if audit.overall_issues:
        lines.extend(["", "## Document-level issues", ""])
        for issue in audit.overall_issues:
            lines.append(
                f"- **[{issue.severity}] {issue.category}**: {issue.issue}"
            )
            if issue.suggestion:
                lines.append(f"  - _Fix:_ {issue.suggestion}")

    lines.extend(["", "## Sections", ""])
    for section in audit.sections:
        lines.append(
            f"### {section.section_title} "
            f"({section.word_count:,} words, priority: {section.revision_priority})"
        )
        if section.structural_role:
            lines.append(f"Structural role: **{section.structural_role}**")
        lines.append("")

        if section.strengths:
            lines.append("_Strengths:_")
            for s in section.strengths:
                lines.append(f"- {s}")
            lines.append("")

        if section.issues:
            lines.append("_Issues:_")
            for issue in section.issues:
                lines.append(
                    f"- **[{issue.severity}] {issue.category}**: {issue.issue}"
                )
                if issue.original:
                    orig = issue.original[:120]
                    lines.append(f"  - _Original:_ \"{orig}\"")
                if issue.suggestion:
                    lines.append(f"  - _Fix:_ {issue.suggestion}")
            lines.append("")

    return "\n".join(lines)
