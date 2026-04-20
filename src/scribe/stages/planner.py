"""Planner stage — reads all inputs and produces a chunking plan."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from scribe.config import ScribeConfig
from scribe.models import Plan
from scribe.parsers.refs import extract_all_refs
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke
from scribe.stages.prompts import planner_prompt

logger = logging.getLogger(__name__)

MAX_JSON_RETRIES = 2


async def run_planner(
    project: Project,
    config: ScribeConfig,
    stream_callback: StreamCallback | None = None,
    review_context: str = "",
) -> Plan:
    """Run the planner stage.

    1. Load outline, style, and all ref texts.
    2. Pre-extract refs to cache for executor access.
    3. Build planner prompt (with review context if available).
    4. Invoke Opus via SDK.
    5. Parse JSON response into Plan model.
    6. Save plan.json and plan_review.md.
    """
    # Load inputs. extract_all_refs writes the per-ref cache that executor
    # uses, so we skip the old duplicate extract_ref_to_cache loop.
    outline_text = project.load_outline()

    # Fail loud on the default scaffold. Running generation on an empty
    # placeholder produces a meta-document about writing, not a real paper.
    # See src/scribe/parsers/sections.py::is_default_scaffold.
    from scribe.parsers.sections import (
        is_default_scaffold,
        looks_like_substantive_draft,
    )
    if is_default_scaffold(outline_text):
        raise ValueError(
            "outline.md is still the default scaffold shipped by `scribe init`. "
            "Replace it with a real outline before running the generation pipeline. "
            "If you want to revise an existing draft rather than generate from an "
            "outline, use `scribe revise` instead."
        )
    if looks_like_substantive_draft(outline_text):
        logger.warning(
            "outline.md looks like a substantive draft (%d words, with citations). "
            "You may have meant `scribe revise` rather than `scribe run`. "
            "Continuing with generation anyway.",
            len(outline_text.split()),
        )

    style_text = project.load_style()
    project.ensure_dirs()
    ref_texts = extract_all_refs(project.refs_dir, cache_dir=project.extracted_dir)

    # Build prompt
    system, user = planner_prompt(
        outline_text, style_text, ref_texts, config,
        review_context=review_context,
    )

    # Invoke SDK
    response = await invoke(
        prompt=user,
        model=config.planner_model_id,
        system_prompt=system,
        cwd=project.root,
        allowed_tools=[],  # Planner doesn't need tools — all data is in the prompt
        max_turns=1,
        stream_callback=stream_callback,
        callback_id="planner",
    )

    # Parse JSON from response
    plan = _parse_plan_response(response, config, project)

    # Save plan
    project.ensure_dirs()
    plan.save(project.plan_path)

    # Save human-readable review
    review_md = render_plan_review(plan)
    project.plan_review_path.write_text(review_md, encoding="utf-8")

    # Archive to plan_history
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    history_path = project.plan_history_dir / f"plan_{timestamp}.json"
    plan.save(history_path)

    logger.info("Plan saved: %d chunks, ~%d words", len(plan.chunks), plan.estimated_words)
    return plan


def _parse_plan_response(
    response: SDKResponse, config: ScribeConfig, project: Project
) -> Plan:
    """Extract and parse JSON from the SDK response text."""
    text = response.text.strip()

    # Try to extract JSON from markdown fences if present
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1).strip()

    # Try to find JSON object boundaries
    if not text.startswith("{"):
        start = text.find("{")
        if start >= 0:
            text = text[start:]

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract just the first JSON object (model may add text after)
        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Planner returned invalid JSON: {e}\n\nResponse text:\n{response.text[:500]}"
            ) from e

    # Ensure project name
    if "project" not in data:
        data["project"] = config.project_name

    # Ensure created timestamp
    if "created" not in data:
        data["created"] = datetime.now(timezone.utc).isoformat()

    # Ensure estimated_chunks matches actual
    if "chunks" in data:
        data["estimated_chunks"] = len(data["chunks"])

    return Plan.model_validate(data)


def render_plan_review(plan: Plan) -> str:
    """Render a human-readable markdown review of the plan."""
    lines = [
        f"# Plan Review — {plan.project}",
        "",
        f"**Chunks:** {plan.estimated_chunks}  ",
        f"**Estimated words:** {plan.estimated_words:,}  ",
        f"**Created:** {plan.created}",
        "",
        "## Chunks",
        "",
        "| ID | Title | Depth | Words | Sources | Web | Rationale |",
        "|-----|-------|-------|------:|--------:|:---:|-----------|",
    ]

    for c in plan.chunks:
        src_count = len(c.sources)
        web = "Y" if c.web_search else ""
        lines.append(
            f"| {c.id} | {c.title} | {c.depth.value} | {c.words:,} | "
            f"{src_count} | {web} | {c.rationale} |"
        )

    if plan.gaps:
        lines.extend(["", "## Gaps", ""])
        for g in plan.gaps:
            lines.append(f"- **{g.bullet}** — {g.issue}. *Suggestion:* {g.suggestion}")

    if plan.contradictions:
        lines.extend(["", "## Contradictions", ""])
        for c in plan.contradictions:
            lines.append(f"- {c}")

    if plan.restructure_suggestions:
        lines.extend(["", "## Restructure Suggestions", ""])
        for s in plan.restructure_suggestions:
            lines.append(f"- {s}")

    lines.append("")
    return "\n".join(lines)
