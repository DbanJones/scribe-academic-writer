"""Stitcher stage — smooths drafts into a final document."""

from __future__ import annotations

import logging
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import Plan
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke
from scribe.stages.prompts import stitcher_prompt

logger = logging.getLogger(__name__)


async def run_stitcher(
    project: Project,
    plan: Plan,
    config: ScribeConfig,
    stream_callback: StreamCallback | None = None,
) -> Path:
    """Read all draft files in order, stitch into final.md.

    Returns path to final.md.
    """
    # Collect drafts in chunk order
    draft_texts: dict[str, str] = {}
    for chunk in plan.chunks:
        draft_path = project.draft_path(chunk)
        if not draft_path.exists():
            logger.warning("Draft missing for chunk %s: %s", chunk.id, draft_path)
            continue
        draft_texts[chunk.id] = draft_path.read_text(encoding="utf-8")

    if not draft_texts:
        raise FileNotFoundError("No draft files found. Run 'scribe write' first.")

    outline_text = project.load_outline()
    style_text = project.load_style()

    system, user = stitcher_prompt(draft_texts, style_text, outline_text)

    response = await invoke(
        prompt=user,
        model=config.stitcher_model_id,
        system_prompt=system,
        cwd=project.root,
        allowed_tools=[],  # Stitcher doesn't need tools
        max_turns=1,
        stream_callback=stream_callback,
        callback_id="stitcher",
    )

    if response.is_error:
        raise RuntimeError(f"Stitcher failed: {response.text[:500]}")

    project.final_path.write_text(response.text, encoding="utf-8")
    logger.info(
        "Final document: %d words at %s",
        len(response.text.split()),
        project.final_path,
    )
    return project.final_path
