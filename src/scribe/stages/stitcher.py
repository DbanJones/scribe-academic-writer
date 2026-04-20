"""Stitcher stage — smooths drafts into a final document.

For long documents that would blow past Claude's context window we fall back
to a two-pass strategy: stitch groups of drafts individually, then stitch the
group outputs together. ``STITCH_INPUT_TOKEN_BUDGET`` is the threshold at
which we switch strategies.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import Plan
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke
from scribe.stages.prompts import stitcher_prompt
from scribe.tokens import estimate_tokens

logger = logging.getLogger(__name__)

# Budget for user + system prompt combined. Opus supports ~200k, so this
# leaves generous headroom for the response and any tool overhead.
STITCH_INPUT_TOKEN_BUDGET = 160_000


async def run_stitcher(
    project: Project,
    plan: Plan,
    config: ScribeConfig,
    stream_callback: StreamCallback | None = None,
    review_context: str = "",
) -> Path:
    """Read all draft files in order, stitch into final.md.

    Returns path to final.md.
    """
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

    final_text = await _stitch_with_budget(
        draft_texts=draft_texts,
        style_text=style_text,
        outline_text=outline_text,
        review_context=review_context,
        config=config,
        cwd=project.root,
        stream_callback=stream_callback,
    )

    project.final_path.write_text(final_text, encoding="utf-8")
    logger.info(
        "Final document: %d words at %s",
        len(final_text.split()),
        project.final_path,
    )

    # Auto-export a Word version alongside the markdown. Non-fatal on failure.
    from scribe.export import try_export_sibling
    try_export_sibling(project.final_path)

    return project.final_path


async def _stitch_with_budget(
    draft_texts: dict[str, str],
    style_text: str,
    outline_text: str,
    review_context: str,
    config: ScribeConfig,
    cwd: Path,
    stream_callback: StreamCallback | None,
) -> str:
    system, user = stitcher_prompt(
        draft_texts, style_text, outline_text, review_context=review_context,
    )
    total_tokens = estimate_tokens(system) + estimate_tokens(user)

    if total_tokens <= STITCH_INPUT_TOKEN_BUDGET:
        return await _invoke_stitcher(
            system, user, config=config, cwd=cwd,
            stream_callback=stream_callback, callback_id="stitcher",
        )

    logger.warning(
        "Stitcher input ~%d tokens exceeds budget %d; using group-then-merge strategy.",
        total_tokens, STITCH_INPUT_TOKEN_BUDGET,
    )

    groups = _group_drafts_by_budget(
        draft_texts, style_text, outline_text, review_context,
        budget=STITCH_INPUT_TOKEN_BUDGET,
    )
    logger.info("Stitching %d draft groups before final merge.", len(groups))

    group_outputs: list[str] = []
    for i, group in enumerate(groups, 1):
        group_system, group_user = stitcher_prompt(
            group, style_text, outline_text, review_context=review_context,
        )
        group_text = await _invoke_stitcher(
            group_system, group_user, config=config, cwd=cwd,
            stream_callback=stream_callback, callback_id=f"stitcher-g{i}",
        )
        group_outputs.append(group_text)

    # Final merge pass: feed the stitched group outputs back in as pseudo-
    # chunks. If even this is too big, concatenate with boundary dividers
    # and call that the final (the first pass already enforced style).
    merge_drafts = {f"group_{i}": txt for i, txt in enumerate(group_outputs, 1)}
    merge_system, merge_user = stitcher_prompt(
        merge_drafts, style_text, outline_text, review_context=review_context,
    )
    if estimate_tokens(merge_system) + estimate_tokens(merge_user) > STITCH_INPUT_TOKEN_BUDGET:
        logger.warning("Merge pass also over budget; concatenating group outputs directly.")
        return "\n\n".join(group_outputs)

    return await _invoke_stitcher(
        merge_system, merge_user, config=config, cwd=cwd,
        stream_callback=stream_callback, callback_id="stitcher-merge",
    )


def _group_drafts_by_budget(
    draft_texts: dict[str, str],
    style_text: str,
    outline_text: str,
    review_context: str,
    *,
    budget: int,
) -> list[dict[str, str]]:
    """Greedy pack: add drafts to a group until adding another would bust the
    token budget, then start a new group. Each group preserves draft order.
    """
    # Measure the fixed overhead of the stitcher prompt with no drafts
    empty_system, empty_user = stitcher_prompt(
        {}, style_text, outline_text, review_context=review_context,
    )
    overhead = estimate_tokens(empty_system) + estimate_tokens(empty_user)

    groups: list[dict[str, str]] = []
    current: dict[str, str] = {}
    current_tokens = overhead

    for cid, text in draft_texts.items():
        cost = estimate_tokens(text) + 20  # small allowance for the divider header
        if current and current_tokens + cost > budget:
            groups.append(current)
            current = {}
            current_tokens = overhead
        current[cid] = text
        current_tokens += cost

    if current:
        groups.append(current)
    return groups


async def _invoke_stitcher(
    system: str,
    user: str,
    *,
    config: ScribeConfig,
    cwd: Path,
    stream_callback: StreamCallback | None,
    callback_id: str,
) -> str:
    response = await invoke(
        prompt=user,
        model=config.stitcher_model_id,
        system_prompt=system,
        cwd=cwd,
        allowed_tools=[],
        max_turns=1,
        stream_callback=stream_callback,
        callback_id=callback_id,
    )
    if response.is_error:
        raise RuntimeError(f"Stitcher ({callback_id}) failed: {response.text[:500]}")
    return response.text
