"""All prompt templates and the plan JSON schema."""

from __future__ import annotations

from scribe.config import ScribeConfig

PLAN_JSON_SCHEMA = """\
{
  "type": "object",
  "required": ["project", "created", "estimated_words", "estimated_chunks", "chunks"],
  "properties": {
    "project": {"type": "string"},
    "created": {"type": "string", "format": "date-time"},
    "estimated_words": {"type": "integer"},
    "estimated_chunks": {"type": "integer"},
    "chunks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "title", "covers", "depth", "words"],
        "properties": {
          "id": {"type": "string", "description": "Short ID like c1, c2, ..."},
          "title": {"type": "string"},
          "covers": {"type": "array", "items": {"type": "string"}, "description": "Section/heading names this chunk covers"},
          "depth": {"type": "string", "enum": ["skim", "standard", "deep", "rigorous"]},
          "words": {"type": "integer", "description": "Target word count"},
          "sources": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "file": {"type": "string"},
                "focus": {"type": "string", "default": "whole document"}
              },
              "required": ["file"]
            }
          },
          "web_search": {"type": "boolean", "default": false},
          "visuals": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "suggested_location": {"type": "string"},
                "type": {"type": "string"},
                "purpose": {"type": "string"}
              }
            }
          },
          "rationale": {"type": "string"}
        }
      }
    },
    "gaps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "bullet": {"type": "string"},
          "issue": {"type": "string"},
          "suggestion": {"type": "string"}
        }
      }
    },
    "contradictions": {"type": "array", "items": {"type": "string"}},
    "restructure_suggestions": {"type": "array", "items": {"type": "string"}}
  }
}"""


ACADEMIC_RULES_PREAMBLE = """\
ACADEMIC WRITING QUALITY RULES (apply these throughout):
- Use concrete nouns and vivid verbs; avoid nominalizations where the verb form is clearer
- Keep subjects and verbs close together (within 10-12 words)
- Prefer active voice unless passive serves topic continuity or the agent is irrelevant
- Put actions in verbs, not in abstract nouns paired with empty verbs (avoid "perform an analysis" -> "analyse")
- Begin sentences with familiar information, end with new/important information (old-to-new flow)
- Omit needless words: replace wordy phrases with concise alternatives
- Vary sentence length for rhythm; flag any sentence over 40 words for splitting
- Each paragraph: one idea, topic sentence first, 3-6 sentences, concluding thought
- Use parallel construction in lists and series
- State claims positively rather than negatively where possible
- Hedge empirical claims appropriately ("suggests", "indicates") but be direct on established facts
- Weave citations into your argument; never string citations without synthesis
- Minimise metadiscourse ("In this section we will discuss...") -- keep signposting lean
- No clutter: "due to the fact that" -> "because", "in order to" -> "to", "utilize" -> "use"
- No unnecessary qualifiers: cut "very", "quite", "rather", "somewhat" unless they add precision
"""


def planner_prompt(
    outline_text: str,
    style_text: str,
    ref_texts: dict[str, str],
    config: ScribeConfig,
    review_context: str = "",
) -> tuple[str, str]:
    """Build the planner prompt.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    review_block = f"\n\n{review_context}\n" if review_context else ""

    system = (
        "You are a document planner for a chunked long-form writing system. "
        "Your job is to read an outline, style guide, and reference materials, "
        "then produce a detailed chunking plan as JSON.\n\n"
        "STYLE GUIDE (follow this exactly in all planning decisions):\n"
        f"{style_text}\n"
        f"{review_block}"
    )

    refs_block = ""
    if ref_texts:
        refs_parts = []
        for path, text in ref_texts.items():
            refs_parts.append(f"### {path}\n\n{text}")
        refs_block = "\n\n---\n\n".join(refs_parts)

    suggest_visuals_instruction = ""
    if config.suggest_visuals:
        suggest_visuals_instruction = (
            "- whether visuals (charts, diagrams, tables) should be suggested "
            "and roughly where in the chunk they belong\n"
        )

    user = f"""\
Read the outline, style guide, and reference materials below. Produce a chunking plan as JSON.

For each chunk decide:
- which outline sections/bullets it covers (use the exact heading text)
- depth level: skim, standard, deep, or rigorous
- target word count
- which reference documents to use and which specific sections/pages of each
- whether web search is needed for recent facts not in the references
{suggest_visuals_instruction}- a one-line rationale for the chunking decision

{"If a document thesis analysis is provided above, use it to inform your chunking decisions. Ensure each chunk's scope and depth serve the overall argument, problem statement, and key question." if review_context else ""}

Also produce a document-level review noting:
- bullets with no supporting source (gaps)
- contradictions between sources
- structural suggestions (merge, split, reorder)
- overall estimated word count

Respect any manual tags in the outline as hard overrides:
- [depth:X] forces depth on that section
- [ref:path] forces a source
- [ref:path#page-range] forces specific pages
- [web] allows web search for that section
- [skip] excludes from output
- [words:N] forces word count

Default depth for untagged content: {config.default_depth}.
Project name: {config.project_name}.
Citation style: {config.citation_style}.

---

## OUTLINE

{outline_text}

---

## REFERENCE MATERIALS

{refs_block if refs_block else "(No reference materials provided.)"}

---

Output ONLY valid JSON matching this schema. No markdown fences, no commentary before or after the JSON.

{PLAN_JSON_SCHEMA}
"""
    return system, user


def executor_chunk_prompt(
    chunk_id: str,
    chunk_title: str,
    outline_bullets: str,
    depth: str,
    target_words: int,
    sources: list[dict[str, str]],
    web_search: bool,
    visuals: list[dict[str, str]],
    style_text: str,
    config: ScribeConfig,
    review_context: str = "",
    chunk_role: str = "",
    chunk_answers_by: str = "",
) -> tuple[str, str]:
    """Build the per-chunk executor prompt.

    The executor does NOT get ref text injected. It reads refs via SDK tools.
    References have been pre-extracted to .scribe/cache/extracted/ as .md files.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    review_block = f"\n\n{review_context}\n" if review_context else ""

    system = (
        "You are a prose writer for a chunked long-form document. "
        "Write exactly the chunk described below. Follow the style guide exactly.\n\n"
        "STYLE GUIDE:\n"
        f"{style_text}\n\n"
        f"{ACADEMIC_RULES_PREAMBLE}"
        f"{review_block}"
    )

    source_instructions = ""
    if sources:
        source_lines = []
        for src in sources:
            file_path = src["file"]
            focus = src.get("focus", "whole document")
            # Point to the extracted cache version
            cache_path = f".scribe/cache/extracted/{_cache_filename(file_path)}"
            source_lines.append(
                f"- Read the file at `{cache_path}` (focus: {focus}). "
                f"Use the Read tool to access it."
            )
        source_instructions = (
            "SOURCE MATERIALS — read these files using the Read tool:\n"
            + "\n".join(source_lines)
            + "\n\n"
        )

    web_instruction = ""
    if web_search:
        web_instruction = (
            "You may use web search for recent facts not covered in the references. "
            "Cite web sources with URL and access date.\n\n"
        )

    visual_instruction = ""
    if visuals:
        visual_instruction = (
            "Where a chart, diagram, or image would help the reader, insert a markdown "
            "image placeholder with a descriptive caption, e.g.:\n"
            "`![SUGGEST: flowchart of ADIEM layer dependencies](suggest)`\n\n"
            "Suggested visual locations from the plan:\n"
        )
        for v in visuals:
            visual_instruction += (
                f"- {v.get('type', 'visual')} at {v.get('suggested_location', 'appropriate location')}: "
                f"{v.get('purpose', '')}\n"
            )
        visual_instruction += "\n"

    role_instruction = ""
    if chunk_role or chunk_answers_by:
        role_instruction = "THIS CHUNK'S ROLE IN THE DOCUMENT:\n"
        if chunk_role:
            role_instruction += f"  Role: {chunk_role}\n"
        if chunk_answers_by:
            role_instruction += f"  Answers the key question by: {chunk_answers_by}\n"
        role_instruction += (
            "Keep this role in mind -- every paragraph should serve the overall argument.\n\n"
        )

    user = f"""\
Write chunk `{chunk_id}`: "{chunk_title}".

{role_instruction}{source_instructions}{web_instruction}{visual_instruction}\
Outline bullets to expand:
{outline_bullets}

Target length: {target_words} words.
Depth: {depth}.
Citation style: {config.citation_style}.

Write the prose now. Do not include the chunk title as a heading in your output; \
the stitcher adds headings. Write only the body prose for this section.
Do not add meta-commentary about the writing process.
"""
    return system, user


def stitcher_prompt(
    draft_texts: dict[str, str],
    style_text: str,
    outline_text: str,
    review_context: str = "",
) -> tuple[str, str]:
    """Build the stitcher prompt.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    review_block = f"\n\n{review_context}\n" if review_context else ""

    system = (
        "You are stitching a long-form document from sectional drafts. "
        "Follow the style guide exactly.\n\n"
        "STYLE GUIDE:\n"
        f"{style_text}\n\n"
        f"{ACADEMIC_RULES_PREAMBLE}"
        f"{review_block}"
    )

    drafts_block = ""
    for chunk_id, text in draft_texts.items():
        drafts_block += f"\n\n--- CHUNK {chunk_id} ---\n\n{text}"

    user = f"""\
Read each draft chunk in order below. Produce the final document with:
- appropriate heading hierarchy derived from the outline
- smoothed transitions between chunks (remove redundant framings, add connective tissue where needed)
- consistent voice and terminology across the whole document
- no repetition of points made earlier
- preserved citations exactly as written (do not reword them)
- preserved visual suggestion placeholders exactly (lines containing `![SUGGEST:...`)
- consolidated bibliography at the end if cited sources are used
{"- ensure the narrative arc from the thesis analysis is maintained throughout" if review_context else ""}
{"- verify each section serves its identified role in the overall argument" if review_context else ""}

Do not change substantive content, only smooth joins and enforce consistency.

## OUTLINE (for heading structure reference)

{outline_text}

## DRAFTS

{drafts_block}

Output the final document as clean markdown. No meta-commentary.
"""
    return system, user


def _cache_filename(ref_path: str) -> str:
    """Convert a ref path like 'refs/data.xlsx' to 'data.md'."""
    from pathlib import PurePosixPath

    return PurePosixPath(ref_path).stem + ".md"
