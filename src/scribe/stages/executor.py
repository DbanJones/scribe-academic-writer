"""Executor stage — writes chunks in parallel using Sonnet."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from scribe.config import ScribeConfig
from scribe.models import Chunk, ChunkStatus, Plan, RunState
from scribe.parsers.outline import parse_outline, section_text_for_chunk
from scribe.parsers.refs import extract_ref_to_cache
from scribe.project import Project
from scribe.sdk import SDKResponse, StreamCallback, invoke, invoke_parallel
from scribe.stages.prompts import executor_chunk_prompt
from scribe.state import init_run_state, save_state, update_chunk_state

logger = logging.getLogger(__name__)


async def run_executor(
    project: Project,
    plan: Plan,
    config: ScribeConfig,
    run_state: RunState | None = None,
    stream_callback: StreamCallback | None = None,
    chunk_filter: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Execute all (or filtered) chunks in parallel.

    Returns list of draft file paths written.
    """
    project.ensure_dirs()
    outline_text = project.load_outline()
    style_text = project.load_style()
    sections = parse_outline(outline_text)

    # Pre-extract refs to cache
    for ref_path in project.list_refs():
        cache_path = project.extracted_dir / (ref_path.stem + ".md")
        if not cache_path.exists() or force:
            extract_ref_to_cache(ref_path, project.extracted_dir)

    # Determine which chunks to run
    if chunk_filter:
        chunks = [c for c in plan.chunks if c.id == chunk_filter]
        if not chunks:
            raise ValueError(f"Chunk '{chunk_filter}' not found in plan")
    else:
        chunks = plan.chunks

    # Filter to pending/failed if we have state and not forcing
    if run_state and not force:
        from scribe.state import pending_chunks as get_pending
        pending_ids = set(get_pending(run_state))
        chunks = [c for c in chunks if c.id in pending_ids]

    if not chunks:
        logger.info("No chunks to execute")
        return []

    # Init state if needed
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M")
    if run_state is None:
        run_state = init_run_state(plan, run_id)

    # Build tasks for parallel execution
    tasks: list[dict[str, Any]] = []
    for chunk in chunks:
        outline_bullets = section_text_for_chunk(sections, chunk.covers)

        sources = [{"file": s.file, "focus": s.focus} for s in chunk.sources]
        visuals = [
            {
                "suggested_location": v.suggested_location,
                "type": v.type,
                "purpose": v.purpose,
            }
            for v in chunk.visuals
        ]

        system, user = executor_chunk_prompt(
            chunk_id=chunk.id,
            chunk_title=chunk.title,
            outline_bullets=outline_bullets,
            depth=chunk.depth.value,
            target_words=chunk.words,
            sources=sources,
            web_search=chunk.web_search,
            visuals=visuals,
            style_text=style_text,
            config=config,
        )

        tasks.append({
            "prompt": user,
            "model": config.executor_model_id,
            "system_prompt": system,
            "cwd": project.root,
            "allowed_tools": ["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
            "callback_id": chunk.id,
        })

    # Mark chunks as running
    for chunk in chunks:
        update_chunk_state(run_state, chunk.id, ChunkStatus.RUNNING)
    save_state(run_state, project.state_path)

    # Execute in parallel
    start_time = time.time()
    responses = await invoke_parallel(
        tasks,
        max_concurrent=config.parallelism,
        stream_callback=stream_callback,
    )

    # Process results
    draft_paths: list[Path] = []
    for chunk, response in zip(chunks, responses):
        elapsed = time.time() - start_time
        draft_path = project.draft_path(chunk)

        if response.is_error:
            update_chunk_state(
                run_state,
                chunk.id,
                ChunkStatus.FAILED,
                error=response.text[:500],
                duration_s=elapsed,
            )
            logger.error("Chunk %s failed: %s", chunk.id, response.text[:200])
        else:
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(response.text, encoding="utf-8")
            word_count = len(response.text.split())

            update_chunk_state(
                run_state,
                chunk.id,
                ChunkStatus.DONE,
                word_count=word_count,
                duration_s=response.duration_ms / 1000,
            )
            draft_paths.append(draft_path)
            logger.info(
                "Chunk %s done: %d words in %.1fs",
                chunk.id, word_count, response.duration_ms / 1000,
            )

    # Update aggregate token counts
    run_state.tokens_in += sum(r.usage.get("input_tokens", 0) for r in responses if not r.is_error)
    run_state.tokens_out += sum(r.usage.get("output_tokens", 0) for r in responses if not r.is_error)

    save_state(run_state, project.state_path)
    return draft_paths
