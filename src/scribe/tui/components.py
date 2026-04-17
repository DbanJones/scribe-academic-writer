"""Shared Rich renderables and formatters."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

STATUS_ICONS = {
    "pending": "o",
    "running": "*",
    "done": "+",
    "failed": "x",
}

STATUS_STYLES = {
    "pending": "dim",
    "running": "bold cyan",
    "done": "bold green",
    "failed": "bold red",
}


def status_icon(status: str) -> Text:
    icon = STATUS_ICONS.get(status, "?")
    style = STATUS_STYLES.get(status, "")
    return Text(icon, style=style)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs:02d}s"


def format_words(count: int | None) -> str:
    if count is None:
        return ""
    return f"{count:,}w"


def header_panel(project_name: str, subtitle: str = "") -> Panel:
    title = Text(f"SCRIBE  -  {project_name}", style="bold white")
    if subtitle:
        title.append(f"  {subtitle}", style="dim")
    return Panel(title, border_style="blue")


def token_summary_line(
    tokens_in: int, tokens_out: int, cost: float
) -> Text:
    text = Text()
    text.append(f"Tokens: ~{tokens_in:,} in  -  ~{tokens_out:,} out", style="dim")
    if cost > 0:
        text.append(f"  -  est cost: ${cost:.2f}", style="dim yellow")
    return text
