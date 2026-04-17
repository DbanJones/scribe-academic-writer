"""Live execution TUI with per-chunk streaming progress."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskID, TextColumn
from rich.table import Table
from rich.text import Text

from scribe.models import Plan
from scribe.tui.components import (
    format_duration,
    format_words,
    header_panel,
    status_icon,
    token_summary_line,
)


@dataclass
class ChunkDisplay:
    chunk_id: str
    title: str
    target_words: int
    status: str = "pending"
    words_so_far: int = 0
    duration_s: float | None = None
    tool_activities: list[str] = field(default_factory=list)
    error: str | None = None


class ExecutionTUI:
    """Live Rich TUI for monitoring chunk execution."""

    MAX_TOOL_LINES = 3

    def __init__(self, plan: Plan, verbose: bool = True) -> None:
        self.plan = plan
        self.verbose = verbose
        self.console = Console()
        self._chunks: dict[str, ChunkDisplay] = {}
        self._start_time = time.time()
        self._tokens_in = 0
        self._tokens_out = 0
        self._cost = 0.0
        self._live: Live | None = None

        for chunk in plan.chunks:
            self._chunks[chunk.id] = ChunkDisplay(
                chunk_id=chunk.id,
                title=chunk.title,
                target_words=chunk.words,
            )

    async def stream_callback(
        self, chunk_id: str, event_type: str, data: dict[str, Any]
    ) -> None:
        """StreamCallback compatible with sdk.invoke_parallel."""
        display = self._chunks.get(chunk_id)
        if not display:
            return

        if event_type == "text":
            total_text = data.get("total", "")
            display.words_so_far = len(total_text.split())

        elif event_type == "tool_use":
            tool_name = data.get("name", "unknown")
            tool_input = data.get("input", {})
            description = _describe_tool_use(tool_name, tool_input)
            display.tool_activities.append(description)
            if len(display.tool_activities) > self.MAX_TOOL_LINES:
                display.tool_activities = display.tool_activities[-self.MAX_TOOL_LINES:]

        elif event_type == "result":
            display.status = "failed" if data.get("is_error") else "done"
            display.duration_s = data.get("duration_ms", 0) / 1000
            self._cost += data.get("cost", 0)

        self._refresh()

    def mark_chunk_running(self, chunk_id: str) -> None:
        if chunk_id in self._chunks:
            self._chunks[chunk_id].status = "running"
            self._refresh()

    def mark_chunk_done(self, chunk_id: str, words: int, duration: float) -> None:
        if chunk_id in self._chunks:
            d = self._chunks[chunk_id]
            d.status = "done"
            d.words_so_far = words
            d.duration_s = duration
            self._refresh()

    def mark_chunk_failed(self, chunk_id: str, error: str) -> None:
        if chunk_id in self._chunks:
            d = self._chunks[chunk_id]
            d.status = "failed"
            d.error = error
            self._refresh()

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Group:
        elapsed = time.time() - self._start_time
        done_count = sum(1 for d in self._chunks.values() if d.status == "done")
        total = len(self._chunks)

        # Header
        header = header_panel(
            self.plan.project,
            f"Elapsed: {format_duration(elapsed)}",
        )

        # Overall progress
        pct = (done_count / total * 100) if total else 0
        progress_text = Text(
            f"Overall: {done_count} of {total} chunks done ({pct:.0f}%)",
            style="bold",
        )

        # Token line
        tokens = token_summary_line(self._tokens_in, self._tokens_out, self._cost)

        # Chunk table
        table = Table(show_header=True, header_style="bold", expand=True, padding=(0, 1))
        table.add_column("", width=2)
        table.add_column("ID", width=5)
        table.add_column("Title", ratio=3)
        table.add_column("Words", width=8, justify="right")
        table.add_column("Time", width=8, justify="right")

        for display in self._chunks.values():
            icon = status_icon(display.status)

            if display.status == "done":
                words = format_words(display.words_so_far)
            elif display.status == "running":
                words = f"{display.words_so_far}..." if display.words_so_far else ""
            else:
                words = ""

            dur = format_duration(display.duration_s) if display.status == "done" else (
                "ongoing" if display.status == "running" else ""
            )

            table.add_row(icon, display.chunk_id, display.title, words, dur)

            # Show tool activity lines for running chunks
            if self.verbose and display.status == "running" and display.tool_activities:
                for activity in display.tool_activities[-self.MAX_TOOL_LINES:]:
                    table.add_row(
                        "", "", Text(f"  |- {activity}", style="dim"), "", ""
                    )

            # Show error for failed chunks
            if display.status == "failed" and display.error:
                table.add_row(
                    "", "", Text(f"  |- {display.error[:80]}", style="red"), "", ""
                )

        return Group(header, progress_text, tokens, table)

    def start(self) -> None:
        self._start_time = time.time()
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None


def _describe_tool_use(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Produce a short human-readable description of a tool call."""
    if tool_name == "Read":
        path = tool_input.get("file_path", "?")
        return f"reading {_short_path(path)}"
    if tool_name == "Grep":
        pattern = tool_input.get("pattern", "?")
        return f'grep "{pattern}"'
    if tool_name == "Glob":
        pattern = tool_input.get("pattern", "?")
        return f"glob {pattern}"
    if tool_name in ("WebSearch", "WebFetch"):
        query = tool_input.get("query", tool_input.get("url", "?"))
        return f"web: {query[:60]}"
    return f"{tool_name}"


def _short_path(path: str) -> str:
    """Shorten a file path for display."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) > 3:
        return "/".join(parts[-3:])
    return path
