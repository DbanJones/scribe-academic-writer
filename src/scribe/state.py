"""State management for resume capability."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from scribe.models import ChunkState, ChunkStatus, Plan, RunState


def init_run_state(plan: Plan, run_id: str) -> RunState:
    """Create a fresh RunState with all chunks PENDING."""
    return RunState(
        stage="executing",
        run_id=run_id,
        plan_approved=True,
        chunks=[
            ChunkState(chunk_id=c.id)
            for c in plan.chunks
        ],
        started_at=datetime.now(timezone.utc),
    )


def update_chunk_state(
    state: RunState,
    chunk_id: str,
    status: ChunkStatus,
    word_count: int | None = None,
    duration_s: float | None = None,
    error: str | None = None,
) -> None:
    """Update a specific chunk's state in place."""
    for cs in state.chunks:
        if cs.chunk_id == chunk_id:
            cs.status = status
            if word_count is not None:
                cs.word_count = word_count
            if duration_s is not None:
                cs.duration_s = duration_s
            if error is not None:
                cs.error = error
            return
    raise ValueError(f"Chunk {chunk_id} not found in run state")


def pending_chunks(state: RunState) -> list[str]:
    """Return chunk IDs that are PENDING or FAILED (for resume)."""
    return [
        cs.chunk_id
        for cs in state.chunks
        if cs.status in (ChunkStatus.PENDING, ChunkStatus.FAILED)
    ]


def save_state(state: RunState, path: Path) -> None:
    state.save(path)


def load_state(path: Path) -> RunState:
    return RunState.load(path)
