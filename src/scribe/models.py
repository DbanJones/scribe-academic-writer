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


# --- Document review (thesis analysis) ---


class ChunkThemeMapping(BaseModel):
    """How a section/chunk relates to the overall thesis."""
    section: str
    role: str  # e.g. "establishes the problem", "provides evidence for..."
    themes_addressed: list[str] = Field(default_factory=list)
    answers_question_by: str = ""  # how this chunk answers the key question


class DocumentReview(BaseModel):
    """Pre-planning thesis analysis produced by Opus."""
    problem_statement: str = ""
    need_for_resolution: str = ""
    existing_gap: str = ""
    key_question: str = ""
    key_themes: list[str] = Field(default_factory=list)
    theme_descriptions: dict[str, str] = Field(default_factory=dict)
    section_mappings: list[ChunkThemeMapping] = Field(default_factory=list)
    narrative_arc: str = ""  # how the document should flow as a whole
    tone_guidance: str = ""  # overall tone/register observations

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> DocumentReview:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def as_prompt_context(self) -> str:
        """Render the review as a concise prompt block for downstream stages."""
        lines = [
            "DOCUMENT THESIS ANALYSIS (maintain this frame throughout):",
            "",
            f"PROBLEM STATEMENT: {self.problem_statement}",
            f"NEED FOR RESOLUTION: {self.need_for_resolution}",
            f"EXISTING GAP: {self.existing_gap}",
            f"KEY QUESTION: {self.key_question}",
            "",
            "KEY THEMES:",
        ]
        for theme in self.key_themes:
            desc = self.theme_descriptions.get(theme, "")
            lines.append(f"  - {theme}: {desc}" if desc else f"  - {theme}")

        lines.append("")
        lines.append(f"NARRATIVE ARC: {self.narrative_arc}")

        if self.section_mappings:
            lines.append("")
            lines.append("SECTION ROLES:")
            for m in self.section_mappings:
                themes = ", ".join(m.themes_addressed) if m.themes_addressed else "general"
                lines.append(f"  - {m.section}: {m.role} (themes: {themes})")
                if m.answers_question_by:
                    lines.append(f"    Answers question by: {m.answers_question_by}")

        return "\n".join(lines)


# --- Revision audit ---


class AuditIssue(BaseModel):
    """A single issue flagged during document audit."""
    category: str   # nominalisation, weasel_word, clutter, citation_handling,
                    # structure, hedging, passive_voice, formality, precision,
                    # old_to_new_flow, parallel_structure, metadiscourse
    severity: str = "medium"   # high, medium, low
    location: str = ""         # e.g. "Section 2, para 3" or "Overall"
    original: str = ""         # the offending text (quoted, short)
    issue: str                 # short description of what's wrong
    suggestion: str = ""       # how to fix (brief)


class SectionAudit(BaseModel):
    """Audit of a single document section."""
    section_id: str
    section_title: str
    word_count: int = 0
    structural_role: str = ""  # e.g. "Context", "Literature", "Discussion"
    issues: list[AuditIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    revision_priority: str = "medium"  # high, medium, low


class DocumentAudit(BaseModel):
    """Structured audit of an entire document against academic writing rules."""
    title: str = ""
    total_words: int = 0
    section_count: int = 0
    overall_issues: list[AuditIssue] = Field(default_factory=list)
    overall_strengths: list[str] = Field(default_factory=list)
    sections: list[SectionAudit] = Field(default_factory=list)
    hourglass_assessment: str = ""
    six_elements_present: dict[str, str] = Field(default_factory=dict)
    # e.g. {"Context": "strong", "Literature": "present but weak", "Proposal": "missing", ...}
    overall_verdict: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> DocumentAudit:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class SectionRevision(BaseModel):
    """Result of revising a single section."""
    section_id: str
    section_title: str
    original_words: int = 0
    revised_words: int = 0
    revised_text: str = ""
    changes_summary: list[str] = Field(default_factory=list)
    preserved_citations: list[str] = Field(default_factory=list)


# --- Expansion planning ---


class ExpansionTarget(BaseModel):
    """A specific opportunity to add depth to a section."""
    category: str
    # e.g. "underdeveloped_claim", "missing_example", "thin_evidence",
    # "citation_needs_unpacking", "counterargument_missing",
    # "methodology_brief", "mechanism_compressed", "context_missing"
    current_text: str = ""
    # short quote of the compressed passage the expansion targets
    opportunity: str
    # what depth could be added here, in 1-2 sentences
    suggested_words: int = 0
    # rough budget for this specific expansion


class SectionExpansion(BaseModel):
    """Expansion plan for a single section."""
    section_id: str
    section_title: str
    structural_role: str = ""
    current_words: int = 0
    target_words: int = 0
    preserve_notes: list[str] = Field(default_factory=list)
    # Non-negotiable items (claims, data, framework terms, citations) that
    # the expander must carry forward untouched.
    targets: list[ExpansionTarget] = Field(default_factory=list)
    expansion_strategy: str = ""
    # A 1-2 sentence description of the shape of the expansion for this section


class DocumentExpansion(BaseModel):
    """The overall expansion plan for a document."""
    source_title: str = ""
    current_total_words: int = 0
    target_total_words: int = 0
    multiplier: float = 1.0
    overall_strategy: str = ""
    # Narrative: how the expansion deepens the paper without drifting from
    # the author's thesis.
    preserved_framework: list[str] = Field(default_factory=list)
    # Key terms, classifications, or constructs the author coined that must
    # survive expansion unchanged (e.g. "stabilisation vs explosion camps").
    sections: list[SectionExpansion] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> DocumentExpansion:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class SectionExpansionResult(BaseModel):
    """Result of expanding a single section."""
    section_id: str
    section_title: str
    original_words: int = 0
    expanded_words: int = 0
    target_words: int = 0
    expanded_text: str = ""
    preserved_citations: list[str] = Field(default_factory=list)


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
