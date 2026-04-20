"""Revision stitcher -- pass the assembled revised document through Opus for
transition-smoothing and consistency.

This stage is much lighter than the generation stitcher: it does not restructure
or rewrite, it only harmonises transitions between independently revised
sections and catches any stylistic drift across them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.project import Project
from scribe.sdk import StreamCallback, invoke

logger = logging.getLogger(__name__)


def _build_smoothing_prompt(
    revised_document: str,
    style_text: str,
    academic_rules_preamble: str,
) -> tuple[str, str]:
    system = (
        "You are performing a final consistency pass over a document that has "
        "already been revised section by section. Multiple editors worked on "
        "different sections in parallel, so transitions between sections may "
        "be rough and terminology may drift. Your job is to smooth those "
        "seams without rewriting the content.\n\n"
        f"{academic_rules_preamble}\n"
        "STYLE GUIDE:\n"
        f"{style_text}\n"
    )

    user = f"""\
Smooth the document below. You may:

- Adjust the opening sentence of each section to connect more cleanly to the
  previous section (replace abrupt starts with linking phrases where they
  genuinely help; never add empty metadiscourse like "As discussed above").
- Harmonise inconsistent terminology. If the document uses both "data centre"
  and "datacenter", pick one and use it throughout. If it uses both "CAGR"
  and "compound annual growth rate" without definition, fix on first use.
- Fix any remaining AI tells, sycophantic openers, or banned phrases.
- Remove duplicated content that appears across adjacent sections.
- Ensure heading hierarchy is consistent.

You must NOT:
- Add, remove, or alter any citation.
- Change any numeric claim, percentage, date, or unit.
- Introduce new content, new claims, or new sources.
- Restructure the document or change section order.
- Rewrite sections wholesale. If a section is fine, leave it alone.

Output the full smoothed document as clean markdown. No commentary, no summary
of changes. Just the document.

---

{revised_document}
"""
    return system, user


async def run_revision_stitcher(
    project: Project,
    config: ScribeConfig,
    revised_document: str,
    stream_callback: StreamCallback | None = None,
) -> Path:
    """Pass the assembled revised document through Opus for final smoothing."""
    from scribe.stages.prompts import ACADEMIC_RULES_PREAMBLE

    style_text = project.load_style()
    system, user = _build_smoothing_prompt(
        revised_document, style_text, ACADEMIC_RULES_PREAMBLE,
    )

    # Wrap invoke in try/except so a CLI/network failure doesn't nuke the
    # whole pipeline. The revised sections are already on disk and the
    # assembled document captures them; we just skip the smoothing pass.
    final_text = revised_document
    try:
        response = await invoke(
            prompt=user,
            model=config.stitcher_model_id,
            system_prompt=system,
            cwd=project.root,
            allowed_tools=[],
            max_turns=1,
            stream_callback=stream_callback,
            callback_id="revision_stitcher",
        )
        if response.is_error:
            logger.error(
                "Revision stitcher failed: %s. Keeping assembled version.",
                response.text[:300],
            )
        else:
            final_text = response.text.strip()
    except Exception as e:  # noqa: BLE001 -- keep the work we already have
        logger.error(
            "Revision stitcher crashed with %s: %s. Keeping assembled version.",
            type(e).__name__, str(e)[:300],
        )

    # Always write something: either the smoothed version or the assembled fallback
    project.revised_path.write_text(final_text, encoding="utf-8")
    logger.info("Revised document: %d words at %s",
                len(final_text.split()), project.revised_path)
    return project.revised_path
