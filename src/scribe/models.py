"""Pydantic models for plan, state, and structured data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Depth(str, Enum):
    SKIM = "skim"
    STANDARD = "standard"
    DEEP = "deep"
    RIGOROUS = "rigorous"


class ChunkSource(BaseModel):
    file: str
    focus: str = "whole document"


class VisualSuggestion(BaseModel):
    suggested_location: str
    type: str
    purpose: str


class Chunk(BaseModel):
    id: str
    title: str
    covers: list[str]
    depth: Depth = Depth.STANDARD
    words: int = 500
    sources: list[ChunkSource] = Field(default_factory=list)
    web_search: bool = False
    visuals: list[VisualSuggestion] = Field(default_factory=list)
    rationale: str = ""


class PlanGap(BaseModel):
    bullet: str
    issue: str
    suggestion: str


class Plan(BaseModel):
    project: str
    created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    estimated_words: int = 0
    estimated_chunks: int = 0
    chunks: list[Chunk] = Field(default_factory=list)
    gaps: list[PlanGap] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    restructure_suggestions: list[str] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Plan:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


# --- Run state ---


class ChunkStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ChunkState(BaseModel):
    chunk_id: str
    status: ChunkStatus = ChunkStatus.PENDING
    word_count: int | None = None
    duration_s: float | None = None
    error: str | None = None


class RunState(BaseModel):
    stage: str = "planning"
    run_id: str = ""
    plan_approved: bool = False
    chunks: list[ChunkState] = Field(default_factory=list)
    started_at: datetime | None = None
    tokens_in: int = 0
    tokens_out: int = 0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> RunState:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


# --- Token tracking ---


class TokenUsage(BaseModel):
    stage: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    duration_ms: int = 0
    cost_estimate_usd: float = 0.0
