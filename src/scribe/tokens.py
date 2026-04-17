"""Token estimation and cost tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

from scribe.models import TokenUsage

# Use cl100k_base as a reasonable approximation for Claude tokenization
_ENCODING = tiktoken.get_encoding("cl100k_base")

# Approximate API pricing per million tokens (for awareness on Max)
PRICE_INPUT_PER_M = 15.0    # Opus input
PRICE_OUTPUT_PER_M = 75.0   # Opus output
PRICE_CACHED_PER_M = 1.875  # Cached input


def estimate_tokens(text: str) -> int:
    """Estimate token count using tiktoken (rough approximation)."""
    return len(_ENCODING.encode(text))


def estimate_cost(
    tokens_in: int, tokens_out: int, cached_in: int = 0
) -> float:
    """Estimate USD cost as if using API pricing."""
    uncached_in = max(0, tokens_in - cached_in)
    return (
        uncached_in / 1_000_000 * PRICE_INPUT_PER_M
        + cached_in / 1_000_000 * PRICE_CACHED_PER_M
        + tokens_out / 1_000_000 * PRICE_OUTPUT_PER_M
    )


@dataclass
class TokenTracker:
    """Tracks token usage across all stages of a run."""

    usages: list[TokenUsage] = field(default_factory=list)

    def record(
        self,
        stage: str,
        tokens_in: int,
        tokens_out: int,
        cached_in: int = 0,
        duration_ms: int = 0,
    ) -> None:
        cost = estimate_cost(tokens_in, tokens_out, cached_in)
        self.usages.append(TokenUsage(
            stage=stage,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_in=cached_in,
            duration_ms=duration_ms,
            cost_estimate_usd=cost,
        ))

    def total_in(self) -> int:
        return sum(u.tokens_in for u in self.usages)

    def total_out(self) -> int:
        return sum(u.tokens_out for u in self.usages)

    def total_cached(self) -> int:
        return sum(u.cached_in for u in self.usages)

    def total_cost(self) -> float:
        return sum(u.cost_estimate_usd for u in self.usages)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = [u.model_dump() for u in self.usages]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def generate_run_report(
    project_name: str,
    run_id: str,
    duration_s: float,
    plan_chunks: int,
    chunk_results: list[dict],
    tracker: TokenTracker,
) -> str:
    """Generate a run report markdown string."""
    total_words = sum(r.get("word_count", 0) for r in chunk_results)
    done_count = sum(1 for r in chunk_results if r.get("status") == "done")

    lines = [
        "# Scribe Run Report",
        "",
        f"**Project:** {project_name}",
        f"**Run ID:** {run_id}",
        f"**Duration:** {_fmt_duration(duration_s)}",
        f"**Output:** {total_words:,} words across {done_count} chunks",
        "",
        "## Token Usage (estimated)",
        "",
    ]

    for u in tracker.usages:
        cached = f" (cached: {u.cached_in:,})" if u.cached_in else ""
        lines.append(
            f"- {u.stage}: {u.tokens_in:,} in{cached} / {u.tokens_out:,} out"
        )

    lines.extend([
        f"- **Total:** {tracker.total_in():,} in / {tracker.total_out():,} out",
        f"- **Max-equivalent cost estimate:** ${tracker.total_cost():.2f} (if API) / included in Max",
        "",
        "## Chunks",
        "",
    ])

    for r in chunk_results:
        status = r.get("status", "unknown")
        words = r.get("word_count", 0)
        dur = r.get("duration_s", 0)
        icon = "+" if status == "done" else "x"
        lines.append(f"- {icon} {r['chunk_id']} {r.get('title', '')} -- {words:,} words, {_fmt_duration(dur)}")

    lines.append("")
    return "\n".join(lines)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs:02d}s"
