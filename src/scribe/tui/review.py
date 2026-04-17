"""Interactive plan review TUI."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from scribe.models import Chunk, ChunkSource, Depth, Plan, VisualSuggestion
from scribe.project import Project
from scribe.tui.components import header_panel


class ReviewTUI:
    """Interactive plan review with Rich prompts."""

    def __init__(self, plan: Plan, project: Project) -> None:
        self.plan = plan
        self.project = project
        self.console = Console()

    def run(self) -> Plan | None:
        """Main loop. Returns approved Plan, or None if user quit."""
        while True:
            self._display_plan()
            self._display_issues()

            action = Prompt.ask(
                "\n[bold][A]pprove  [E]dit chunk  [D]elete chunk  "
                "[M]erge chunks  [S]how rationale  [Q]uit[/bold]",
                choices=["a", "e", "d", "m", "s", "q"],
                default="a",
            )

            if action == "a":
                self._archive_plan()
                return self.plan
            elif action == "e":
                self._handle_edit()
            elif action == "d":
                self._handle_delete()
            elif action == "m":
                self._handle_merge()
            elif action == "s":
                self._handle_show_rationale()
            elif action == "q":
                if Confirm.ask("Quit without approving?", default=False):
                    return None

    def _display_plan(self) -> None:
        self.console.clear()
        self.console.print(header_panel(self.plan.project, "PLAN REVIEW"))

        total_words = sum(c.words for c in self.plan.chunks)
        self.console.print(
            f"  {len(self.plan.chunks)} chunks, ~{total_words:,} words\n"
        )

        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("ID", width=5)
        table.add_column("Title", ratio=3)
        table.add_column("Depth", width=10)
        table.add_column("Words", width=8, justify="right")
        table.add_column("Sources", width=8, justify="right")
        table.add_column("Web", width=4, justify="center")

        for c in self.plan.chunks:
            web = "Y" if c.web_search else ""
            table.add_row(
                c.id, c.title, c.depth.value,
                f"{c.words:,}", str(len(c.sources)), web,
            )

        self.console.print(table)

    def _display_issues(self) -> None:
        if self.plan.gaps:
            self.console.print(f"\n[yellow]Gaps ({len(self.plan.gaps)}):[/yellow]")
            for g in self.plan.gaps:
                self.console.print(f"  * {g.bullet} -- {g.issue}")

        if self.plan.contradictions:
            self.console.print(
                f"\n[yellow]Contradictions ({len(self.plan.contradictions)}):[/yellow]"
            )
            for c in self.plan.contradictions:
                self.console.print(f"  * {c}")

        if self.plan.restructure_suggestions:
            self.console.print(
                f"\n[yellow]Suggestions ({len(self.plan.restructure_suggestions)}):[/yellow]"
            )
            for s in self.plan.restructure_suggestions:
                self.console.print(f"  * {s}")

        if not (self.plan.gaps or self.plan.contradictions or self.plan.restructure_suggestions):
            self.console.print("\n[green]No issues found.[/green]")

    def _handle_edit(self) -> None:
        chunk_id = Prompt.ask("Chunk ID to edit")
        chunk = self._find_chunk(chunk_id)
        if not chunk:
            self.console.print(f"[red]Chunk '{chunk_id}' not found.[/red]")
            return

        self.console.print(f"\nEditing chunk [bold]{chunk.id}[/bold]: {chunk.title}")
        self.console.print(f"  Current depth: {chunk.depth.value}")
        self.console.print(f"  Current words: {chunk.words}")
        self.console.print(f"  Current sources: {len(chunk.sources)}")

        new_title = Prompt.ask("Title", default=chunk.title)
        depth_str = Prompt.ask(
            "Depth (skim/standard/deep/rigorous)",
            default=chunk.depth.value,
        )
        new_words = IntPrompt.ask("Words", default=chunk.words)

        chunk.title = new_title
        chunk.depth = Depth(depth_str)
        chunk.words = new_words

        # Recalculate totals
        self.plan.estimated_words = sum(c.words for c in self.plan.chunks)
        self.console.print("[green]Chunk updated.[/green]")

    def _handle_delete(self) -> None:
        chunk_id = Prompt.ask("Chunk ID to delete")
        chunk = self._find_chunk(chunk_id)
        if not chunk:
            self.console.print(f"[red]Chunk '{chunk_id}' not found.[/red]")
            return

        if Confirm.ask(f"Delete chunk {chunk.id} ({chunk.title})?", default=False):
            self.plan.chunks = [c for c in self.plan.chunks if c.id != chunk_id]
            self.plan.estimated_chunks = len(self.plan.chunks)
            self.plan.estimated_words = sum(c.words for c in self.plan.chunks)
            self.console.print("[green]Chunk deleted.[/green]")

    def _handle_merge(self) -> None:
        ids_str = Prompt.ask("Chunk IDs to merge (comma-separated, e.g. c2,c3)")
        ids = [i.strip() for i in ids_str.split(",")]

        chunks_to_merge = [c for c in self.plan.chunks if c.id in ids]
        if len(chunks_to_merge) < 2:
            self.console.print("[red]Need at least 2 valid chunk IDs.[/red]")
            return

        # Merge into first chunk
        merged = chunks_to_merge[0]
        for other in chunks_to_merge[1:]:
            merged.covers.extend(other.covers)
            merged.words += other.words
            merged.sources.extend(other.sources)
            if other.web_search:
                merged.web_search = True
            merged.visuals.extend(other.visuals)

        merged.title = Prompt.ask("Merged chunk title", default=merged.title)

        # Remove other chunks
        remove_ids = {c.id for c in chunks_to_merge[1:]}
        self.plan.chunks = [c for c in self.plan.chunks if c.id not in remove_ids]
        self.plan.estimated_chunks = len(self.plan.chunks)
        self.plan.estimated_words = sum(c.words for c in self.plan.chunks)
        self.console.print("[green]Chunks merged.[/green]")

    def _handle_show_rationale(self) -> None:
        self.console.print("\n[bold]Chunk Rationale:[/bold]")
        for c in self.plan.chunks:
            self.console.print(f"  [bold]{c.id}[/bold] {c.title}")
            self.console.print(f"    {c.rationale or '(no rationale)'}")
        Prompt.ask("\nPress Enter to continue", default="")

    def _find_chunk(self, chunk_id: str) -> Chunk | None:
        for c in self.plan.chunks:
            if c.id == chunk_id:
                return c
        return None

    def _archive_plan(self) -> None:
        """Copy current plan.json to plan_history/ before overwriting."""
        if self.project.plan_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = self.project.plan_history_dir / f"plan_{timestamp}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.project.plan_path, dest)

        # Save updated plan
        self.plan.save(self.project.plan_path)
