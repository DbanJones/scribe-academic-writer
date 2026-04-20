"""Scribe CLI — all commands."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(
    name="scribe",
    help="Chunked long-form writing tool powered by Claude.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def init(
    project_dir: Path = typer.Argument(..., help="Path to create or initialise"),
) -> None:
    """Scaffold a new Scribe project folder."""
    from scribe.project import Project

    project = Project.scaffold(project_dir)
    console.print(f"[green]Project initialised at {project.root}[/green]")
    console.print("  outline.md  -- add your document structure here")
    console.print("  style.md    -- customise voice, rules, citations")
    console.print("  refs/       -- drop reference documents here")


@app.command()
def plan(
    project_dir: Path = typer.Argument(..., help="Project folder"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only, no writing"),
) -> None:
    """Run the planner to generate a chunking plan."""
    from scribe.project import Project
    from scribe.stages.planner import run_planner

    project = Project(project_dir)
    config = project.config()
    project.ensure_dirs()

    console.print(f"[bold]Planning: {config.project_name}[/bold]")
    console.print(f"  Outline: {project.outline_path}")
    console.print(f"  Style: {project.style_path}")
    console.print(f"  Refs: {len(project.list_refs())} files")
    console.print()

    result = asyncio.run(run_planner(project, config))

    console.print(
        f"\n[green]Plan created: {result.estimated_chunks} chunks, "
        f"~{result.estimated_words:,} words[/green]"
    )
    console.print(f"  Saved to: {project.plan_path}")
    console.print(f"  Review:   {project.plan_review_path}")

    if result.gaps:
        console.print(f"\n[yellow]Gaps ({len(result.gaps)}):[/yellow]")
        for g in result.gaps:
            console.print(f"  - {g.bullet}: {g.issue}")


@app.command()
def review(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """Open TUI to review and edit an existing plan."""
    from scribe.models import Plan
    from scribe.project import Project
    from scribe.tui.review import ReviewTUI

    project = Project(project_dir)
    if not project.plan_path.exists():
        console.print("[red]No plan.json found. Run 'scribe plan' first.[/red]")
        raise typer.Exit(1)

    loaded_plan = Plan.load(project.plan_path)
    tui = ReviewTUI(loaded_plan, project)
    result = tui.run()

    if result:
        console.print("[green]Plan approved.[/green]")
    else:
        console.print("[yellow]Plan not approved.[/yellow]")


@app.command()
def write(
    project_dir: Path = typer.Argument(..., help="Project folder"),
    chunk: str | None = typer.Option(None, "--chunk", help="Single chunk ID"),
    force: bool = typer.Option(False, "--force", help="Regenerate all chunks"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="TUI verbosity"),
) -> None:
    """Execute the plan and write chunks."""
    from scribe.models import Plan
    from scribe.project import Project
    from scribe.stages.executor import run_executor
    from scribe.tui.progress import ExecutionTUI

    project = Project(project_dir)
    config = project.config()

    if not project.plan_path.exists():
        console.print("[red]No plan.json found. Run 'scribe plan' first.[/red]")
        raise typer.Exit(1)

    loaded_plan = Plan.load(project.plan_path)
    target = f"chunk {chunk}" if chunk else f"all {len(loaded_plan.chunks)} chunks"
    console.print(f"[bold]Writing {target} for: {loaded_plan.project}[/bold]")

    tui = ExecutionTUI(loaded_plan, verbose=verbose)

    async def _run() -> list[Path]:
        tui.start()
        try:
            return await run_executor(
                project,
                loaded_plan,
                config,
                chunk_filter=chunk,
                force=force,
                stream_callback=tui.stream_callback,
            )
        finally:
            tui.stop()

    drafts = asyncio.run(_run())

    console.print(f"\n[green]Done: {len(drafts)} drafts written.[/green]")
    for d in drafts:
        console.print(f"  {d.name}")


@app.command()
def run(
    project_dir: Path = typer.Argument(..., help="Project folder"),
    no_review: bool = typer.Option(False, "--no-review", help="Skip review step"),
    force: bool = typer.Option(False, "--force", help="Regenerate everything"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", help="TUI verbosity"),
) -> None:
    """Full pipeline: plan, review, write, stitch."""
    from scribe.diff import changed_files, chunks_affected_by_changes, compute_hashes, save_hashes
    from scribe.git import ScribeRepo
    from scribe.models import DocumentReview, Plan
    from scribe.project import Project
    from scribe.stages.executor import run_executor
    from scribe.stages.planner import run_planner
    from scribe.stages.reviewer import run_reviewer
    from scribe.stages.stitcher import run_stitcher
    from scribe.tokens import TokenTracker, generate_run_report
    from scribe.tui.progress import ExecutionTUI
    from scribe.tui.review import ReviewTUI

    project = Project(project_dir)
    config = project.config()
    project.ensure_dirs()
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M")
    tracker = TokenTracker()
    pipeline_start = time.time()
    git_repo = ScribeRepo(project.root)

    # --- DIFF DETECTION ---
    changes = changed_files(project) if not force else []
    if changes and not force:
        console.print(f"[dim]Changed inputs: {', '.join(changes)}[/dim]")

    # --- DOCUMENT REVIEW (Opus) ---
    console.print(f"[bold]Stage 1/5: Document Review ({config.project_name})[/bold]")
    console.print(f"  Analysing thesis structure, themes, and argument...\n")

    async def _review_and_plan() -> tuple[DocumentReview, Plan]:
        doc_review = await run_reviewer(project, config)
        review_ctx = doc_review.as_prompt_context()
        console.print(f"[green]Review complete:[/green]")
        console.print(f"  Key question: {doc_review.key_question}")
        console.print(f"  Themes: {', '.join(doc_review.key_themes)}")
        console.print(f"  Sections mapped: {len(doc_review.section_mappings)}\n")

        console.print(f"[bold]Stage 2/5: Planning[/bold]")
        console.print(f"  Outline: {project.outline_path}")
        console.print(f"  Refs: {len(project.list_refs())} files\n")

        the_plan = await run_planner(project, config, review_context=review_ctx)
        return doc_review, the_plan

    doc_review, the_plan = asyncio.run(_review_and_plan())
    review_context = doc_review.as_prompt_context()

    console.print(
        f"[green]Plan: {the_plan.estimated_chunks} chunks, "
        f"~{the_plan.estimated_words:,} words[/green]\n"
    )

    # Git commit after plan
    if config.git.auto_commit:
        git_repo.commit_stage("plan", config.project_name, config.git.commit_template)

    if dry_run:
        console.print("[yellow]Dry run: stopping after plan.[/yellow]")
        return

    # --- REVIEW (interactive TUI, inherently sync) ---
    if not no_review:
        console.print("[bold]Stage 3/5: Review[/bold]\n")
        tui = ReviewTUI(the_plan, project)
        result = tui.run()
        if result is None:
            console.print("[yellow]Aborted.[/yellow]")
            return
        the_plan = result
    else:
        the_plan.save(project.plan_path)

    # Build section mappings for per-chunk role context
    section_mappings = {}
    for m in doc_review.section_mappings:
        section_mappings[m.section] = {
            "role": m.role,
            "answers_question_by": m.answers_question_by,
        }

    # --- WRITE + STITCH in a single event loop ---
    console.print(f"\n[bold]Stage 4/5: Writing {len(the_plan.chunks)} chunks[/bold]\n")
    exec_tui = ExecutionTUI(the_plan, verbose=verbose)

    async def _write_and_stitch() -> tuple[list[Path], Path]:
        exec_tui.start()
        try:
            drafts = await run_executor(
                project,
                the_plan,
                config,
                force=force,
                stream_callback=exec_tui.stream_callback,
                review_context=review_context,
                section_mappings=section_mappings,
            )
        finally:
            exec_tui.stop()
        console.print(f"\n[green]{len(drafts)} drafts written.[/green]\n")

        if config.git.auto_commit:
            git_repo.commit_stage("draft", config.project_name, config.git.commit_template)

        console.print("[bold]Stage 5/5: Stitching[/bold]\n")
        final = await run_stitcher(
            project, the_plan, config, review_context=review_context
        )
        return drafts, final

    drafts, final_path = asyncio.run(_write_and_stitch())

    word_count = len(final_path.read_text(encoding="utf-8").split())
    pipeline_duration = time.time() - pipeline_start

    console.print(
        f"\n[bold green]Done! {word_count:,} words written to {final_path}[/bold green]"
    )
    console.print(f"  Duration: {_fmt_duration(pipeline_duration)}")

    # --- RUN REPORT ---
    run_dir = project.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Gather chunk results from state
    chunk_results = []
    if project.state_path.exists():
        from scribe.state import load_state

        state = load_state(project.state_path)
        for cs in state.chunks:
            matching = [c for c in the_plan.chunks if c.id == cs.chunk_id]
            title = matching[0].title if matching else ""
            chunk_results.append({
                "chunk_id": cs.chunk_id,
                "title": title,
                "status": cs.status.value,
                "word_count": cs.word_count or 0,
                "duration_s": cs.duration_s or 0,
            })

    report = generate_run_report(
        config.project_name,
        run_id,
        pipeline_duration,
        len(the_plan.chunks),
        chunk_results,
        tracker,
    )
    report_path = run_dir / "run_report.md"
    report_path.write_text(report, encoding="utf-8")
    console.print(f"  Report:   {report_path}")

    # Save hashes for diff detection on next run
    save_hashes(project, compute_hashes(project))

    # Git commit after stitch + tag
    if config.git.auto_commit:
        git_repo.commit_stage("final", config.project_name, config.git.commit_template)
        git_repo.tag_run(run_id)


@app.command()
def resume(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """Resume an interrupted run."""
    from scribe.models import Plan
    from scribe.project import Project
    from scribe.stages.executor import run_executor
    from scribe.stages.stitcher import run_stitcher
    from scribe.state import load_state, pending_chunks
    from scribe.tui.progress import ExecutionTUI

    project = Project(project_dir)
    config = project.config()

    if not project.state_path.exists():
        console.print("[red]No state.json found. Nothing to resume.[/red]")
        raise typer.Exit(1)
    if not project.plan_path.exists():
        console.print("[red]No plan.json found.[/red]")
        raise typer.Exit(1)

    state = load_state(project.state_path)
    loaded_plan = Plan.load(project.plan_path)
    pending = pending_chunks(state)

    if not pending:
        console.print("[green]All chunks already complete.[/green]")
        return

    console.print(f"[bold]Resuming: {len(pending)} chunks pending[/bold]")
    exec_tui = ExecutionTUI(loaded_plan, verbose=True)

    async def _resume() -> list[Path]:
        exec_tui.start()
        try:
            return await run_executor(
                project,
                loaded_plan,
                config,
                run_state=state,
                stream_callback=exec_tui.stream_callback,
            )
        finally:
            exec_tui.stop()

    drafts = asyncio.run(_resume())
    console.print(f"\n[green]{len(drafts)} drafts written.[/green]")

    # Re-stitch
    console.print("\n[bold]Re-stitching...[/bold]")
    final_path = asyncio.run(run_stitcher(project, loaded_plan, config))
    console.print(f"[green]Final: {final_path}[/green]")


@app.command()
def stitch(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """Re-stitch existing drafts into final.md."""
    from scribe.models import Plan
    from scribe.project import Project
    from scribe.stages.stitcher import run_stitcher

    project = Project(project_dir)
    config = project.config()

    if not project.plan_path.exists():
        console.print("[red]No plan.json found.[/red]")
        raise typer.Exit(1)

    loaded_plan = Plan.load(project.plan_path)
    console.print(f"[bold]Stitching {len(loaded_plan.chunks)} chunks...[/bold]")

    final_path = asyncio.run(run_stitcher(project, loaded_plan, config))
    word_count = len(final_path.read_text(encoding="utf-8").split())
    console.print(f"[green]Done: {word_count:,} words at {final_path}[/green]")


@app.command()
def status(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """Show current project state and last run info."""
    from scribe.models import Plan
    from scribe.project import Project

    project = Project(project_dir)

    console.print(f"[bold]Project: {project.root}[/bold]\n")

    # Outline
    if project.outline_path.exists():
        text = project.outline_path.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if l.strip()]
        console.print(f"  outline.md: {len(lines)} lines")
    else:
        console.print("  [red]outline.md: missing[/red]")

    # Style
    console.print(
        f"  style.md: {'exists' if project.style_path.exists() else '[yellow]using default[/yellow]'}"
    )

    # Refs
    refs = project.list_refs()
    console.print(f"  refs/: {len(refs)} files")

    # Plan
    if project.plan_path.exists():
        loaded_plan = Plan.load(project.plan_path)
        console.print(
            f"\n  Plan: {loaded_plan.estimated_chunks} chunks, "
            f"~{loaded_plan.estimated_words:,} words"
        )
    else:
        console.print("\n  [dim]No plan yet.[/dim]")

    # State
    if project.state_path.exists():
        from scribe.state import load_state, pending_chunks

        state = load_state(project.state_path)
        pending = pending_chunks(state)
        done = sum(1 for c in state.chunks if c.status.value == "done")
        console.print(f"  State: {done} done, {len(pending)} pending (run: {state.run_id})")

    # Final
    if project.final_path.exists():
        text = project.final_path.read_text(encoding="utf-8")
        console.print(f"\n  [green]final.md: {len(text.split()):,} words[/green]")


@app.command()
def visuals(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """List all visual suggestions from drafts and final."""
    import re

    from scribe.project import Project

    project = Project(project_dir)
    pattern = re.compile(r"!\[SUGGEST:\s*(.+?)\]\(suggest\)")

    suggestions: list[tuple[str, str]] = []

    # Check final.md first
    if project.final_path.exists():
        for match in pattern.finditer(project.final_path.read_text(encoding="utf-8")):
            suggestions.append(("final.md", match.group(1)))

    # Check drafts
    if project.drafts_dir.exists():
        for draft in sorted(project.drafts_dir.glob("*.md")):
            for match in pattern.finditer(draft.read_text(encoding="utf-8")):
                suggestions.append((draft.name, match.group(1)))

    if not suggestions:
        console.print("[dim]No visual suggestions found.[/dim]")
        return

    from rich.table import Table

    table = Table(title="Visual Suggestions", show_header=True)
    table.add_column("Source", width=25)
    table.add_column("Description")

    for source, desc in suggestions:
        table.add_row(source, desc)

    console.print(table)


@app.command()
def history(
    project_dir: Path = typer.Argument(..., help="Project folder"),
) -> None:
    """List recent Scribe runs."""
    from scribe.project import Project

    project = Project(project_dir)
    if not project.runs_dir.exists():
        console.print("[dim]No runs yet.[/dim]")
        return

    runs = sorted(project.runs_dir.iterdir(), reverse=True)
    if not runs:
        console.print("[dim]No runs yet.[/dim]")
        return

    for run_dir in runs[:20]:
        report = run_dir / "run_report.md"
        if report.exists():
            first_lines = report.read_text(encoding="utf-8").splitlines()[:6]
            info = " | ".join(
                l.replace("**", "").strip("- ")
                for l in first_lines
                if l.startswith("**")
            )
            console.print(f"  {run_dir.name}  {info}")
        else:
            console.print(f"  {run_dir.name}  (no report)")


@app.command()
def diff(
    project_dir: Path = typer.Argument(..., help="Project folder"),
    run1: str = typer.Argument(..., help="First run ID"),
    run2: str = typer.Argument(..., help="Second run ID"),
) -> None:
    """Show prose diff between two runs."""
    from scribe.git import ScribeRepo
    from scribe.project import Project

    project = Project(project_dir)
    git_repo = ScribeRepo(project.root)

    if not git_repo.is_initialised():
        console.print("[red]No git repo found.[/red]")
        raise typer.Exit(1)

    result = git_repo.diff_runs(run1, run2)
    if result:
        console.print(result)
    else:
        console.print("[dim]No differences found.[/dim]")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(5000, "--port", help="Port to bind to"),
) -> None:
    """Launch the web UI for project setup and running."""
    from scribe.web.app import run_web

    console.print(f"[bold]Starting Scribe web UI at http://{host}:{port}[/bold]")
    console.print("  Press Ctrl+C to stop.\n")
    run_web(host=host, port=port, debug=False)


@app.command("list-styles")
def list_styles_cmd() -> None:
    """List available citation styles (parents, journals, custom presets)."""
    from scribe.citations import list_styles

    styles = list_styles()
    console.print("[bold]Parent styles[/bold]")
    for s in styles.get("parents", []):
        console.print(f"  {s}")
    console.print("\n[bold]Journal styles[/bold]")
    for s in styles.get("journals", []):
        console.print(f"  {s}")
    if styles.get("custom"):
        console.print("\n[bold]Custom presets[/bold]")
        for s in styles["custom"]:
            console.print(f"  {s}")


@app.command()
def restyle(
    input_file: Path = typer.Argument(..., help="Input .md or .docx file"),
    style: str = typer.Option(..., "--style", "-s", help="Target style id (e.g. apa, ieee, nature)"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output path (default: <input>.restyled-<style>.<ext>)"),
    rules: str = typer.Option("", "--rules", "-r", help="Free-text additional style rules"),
    model: str = typer.Option("opus", "--model", "-m", help="Claude model: opus, sonnet, haiku"),
    no_polish: bool = typer.Option(False, "--no-polish", help="Skip the final formatting polish pass"),
) -> None:
    """Restyle a document's citations and formatting to match a target style."""
    from scribe.citations import load_style
    from scribe.citations.restyler import restyle_document
    from scribe.config import ScribeConfig

    try:
        resolved = load_style(style)
    except FileNotFoundError:
        console.print(f"[red]Unknown style: {style}[/red]")
        console.print("Run [bold]scribe list-styles[/bold] to see available styles.")
        raise typer.Exit(1)

    model_id = ScribeConfig().resolve_model(model)
    console.print(f"[bold]Restyling {input_file.name} -> {resolved['styleName']}[/bold]")
    console.print(f"  Model: {model_id}")
    if rules:
        console.print(f"  Extra rules: {rules[:80]}{'...' if len(rules) > 80 else ''}")

    async def _progress(event: str, payload: dict) -> None:
        console.print(f"  [dim]{event}[/dim] {payload}")

    result = asyncio.run(
        restyle_document(
            input_file,
            style_id=style,
            output_path=output,
            extra_rules=rules,
            model=model_id,
            skip_polish=no_polish,
            progress=_progress,
        )
    )

    console.print(
        f"\n[green]Done: {result.in_text_count} citations, "
        f"{result.bibliography_count} references[/green]"
    )
    console.print(f"  Output: {result.output_path}")
    console.print(f"  Duration: {_fmt_duration(result.duration_s)}")
    if result.cost_usd > 0:
        console.print(f"  Cost: ${result.cost_usd:.4f}")


@app.command()
def revise(
    source: Path = typer.Argument(
        ...,
        help="Path to the existing draft (.md, .docx, .doc, .txt).",
    ),
    output_dir: Path | None = typer.Option(
        None, "--output-dir",
        help="Project folder to create/use (defaults to a .scribe-revise folder next to the source).",
    ),
    audit_only: bool = typer.Option(
        False, "--audit-only",
        help="Run the audit stage only; do not revise or stitch.",
    ),
) -> None:
    """Revise an existing substantive draft against the academic writing rules.

    Unlike `scribe run`, which generates a document from an outline, this
    command takes a finished draft and revises it section by section while
    preserving all citations, data, claims, and argument structure.
    """
    from scribe.project import Project
    from scribe.revision import (
        RevisionInputError,
        load_source_document,
        run_revision_pipeline,
        save_source_upload,
    )
    from scribe.stages.auditor import run_auditor

    source = source.resolve()
    if not source.exists():
        console.print(f"[red]Source not found: {source}[/red]")
        raise typer.Exit(1)

    # Create or reuse a project alongside the source
    project_root = output_dir.resolve() if output_dir else (
        source.parent / f"{source.stem}_revised"
    )
    project_root.mkdir(parents=True, exist_ok=True)
    project = Project(project_root)
    project.ensure_dirs()

    # Copy source into the project under the canonical name
    stored_path = save_source_upload(project, source, source.name)
    console.print(f"[bold]Revising: {source.name}[/bold]")
    console.print(f"  Project: {project.root}")

    config = project.config()

    try:
        if audit_only:
            # Shortcut: just run the auditor and print its summary
            document_text, sections = load_source_document(project)
            console.print(f"  Sections: {len(sections)}, "
                          f"Words: {sum(s.word_count for s in sections):,}\n")
            console.print("[cyan]Running auditor (Opus)...[/cyan]")

            audit = asyncio.run(
                run_auditor(project, config, sections, document_text)
            )
            console.print(
                f"\n[green]Audit complete[/green]: {len(audit.sections)} sections, "
                f"{len(audit.overall_issues)} document-level issues.\n"
            )
            console.print(f"  Audit JSON: {project.audit_path}")
            console.print(f"  Audit summary: {project.audit_summary_path}")
            if audit.overall_verdict:
                console.print(f"\n  [bold]Verdict:[/bold] {audit.overall_verdict}")
            return

        console.print(f"  Config: parallelism={config.parallelism}, "
                      f"reviewer={config.reviewer_model_id}, "
                      f"reviser={config.executor_model_id}\n")
        console.print("[cyan]Running revision pipeline "
                      "(parse -> audit -> revise -> smooth)...[/cyan]\n")

        summary = asyncio.run(run_revision_pipeline(project, config))

        console.print(
            f"[green]Revision complete in {_fmt_duration(summary['duration_s'])}[/green]"
        )
        console.print(
            f"  Words: {summary['original_words']:,} -> {summary['revised_words']:,}"
        )
        console.print(
            f"  Sections revised: {summary['sections_revised']}/{summary['sections_total']}"
        )
        if summary['sections_failed']:
            console.print(
                f"  [yellow]Failed sections: {summary['sections_failed']} "
                f"(originals kept)[/yellow]"
            )
        console.print(f"\n  Revised:       {summary['revised_path']}")
        console.print(f"  Audit (JSON):  {summary['audit_path']}")
        console.print(f"  Audit (MD):    {summary['audit_summary_path']}")
        if summary['audit_verdict']:
            console.print(f"\n  [bold]Audit verdict:[/bold] {summary['audit_verdict']}")

    except RevisionInputError as e:
        console.print(f"[red]Cannot revise: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:  # noqa: BLE001 -- show a readable message, not a wall of traceback
        console.print(f"[red]Revision pipeline crashed:[/red] {type(e).__name__}: {e}")
        console.print(
            f"[yellow]Per-section revisions under {project.revisions_dir} are preserved.[/yellow]"
        )
        console.print(
            "Run with --audit-only to produce an audit without revision, or retry; "
            "if the audit exists, it will be reused and only the revise + smooth stages re-run."
        )
        raise typer.Exit(1)


@app.command()
def export(
    markdown: Path = typer.Argument(
        ...,
        help="Path to the markdown file to export (.md).",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o",
        help="Output .docx path. Defaults to <markdown>.docx.",
    ),
    title: str | None = typer.Option(
        None, "--title",
        help="Document title (used for the Title-styled heading). Defaults to the first H1.",
    ),
) -> None:
    """Convert a markdown file to a formatted Word (.docx) document.

    Preserves heading hierarchy, bullets, numbered lists, tables, inline
    formatting, figure captions, and bibliography entries. Visual suggestion
    placeholders render as boxed callouts.
    """
    from scribe.export import export_file

    if not markdown.exists():
        console.print(f"[red]Not found: {markdown}[/red]")
        raise typer.Exit(1)

    output_path = output or markdown.with_suffix(".docx")
    console.print(f"[bold]Exporting:[/bold] {markdown}")
    try:
        result = export_file(markdown, output_path, title=title)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Export failed:[/red] {type(e).__name__}: {e}")
        raise typer.Exit(1)
    size_kb = result.stat().st_size / 1024
    console.print(f"[green]Wrote:[/green] {result} ({size_kb:.1f} KB)")


@app.command()
def expand(
    source: Path = typer.Argument(
        ...,
        help="Path to the existing draft (.md, .docx, .doc, .txt).",
    ),
    output_dir: Path | None = typer.Option(
        None, "--output-dir",
        help="Project folder to create/use (defaults to <source>_expanded next to the source).",
    ),
    target_words: int | None = typer.Option(
        None, "--target-words",
        help="Target total word count for the expanded document.",
    ),
    multiplier: float | None = typer.Option(
        None, "--multiplier",
        help="Target multiplier (e.g. 2.0 = double). Ignored if --target-words is given.",
    ),
    plan_only: bool = typer.Option(
        False, "--plan-only",
        help="Run the expansion planner only; do not expand or stitch.",
    ),
    no_smooth: bool = typer.Option(
        False, "--no-smooth",
        help="Skip the final smoothing pass (useful for debugging).",
    ),
) -> None:
    """Expand a substantive draft into a longer, deeper version.

    Preserves every existing claim, citation, number, figure, and framework
    term from the source. Expansion deepens compressed passages, surfaces
    implied mechanisms, and unpacks evidence; it does NOT introduce new
    substantive claims the author did not make.
    """
    from scribe.expansion import (
        ExpansionInputError,
        load_source_document_for_expansion,
        run_expansion_pipeline,
        save_source_upload,
    )
    from scribe.project import Project
    from scribe.stages.expander_planner import run_expansion_planner
    from scribe.parsers.refs import extract_all_refs

    source = source.resolve()
    if not source.exists():
        console.print(f"[red]Source not found: {source}[/red]")
        raise typer.Exit(1)

    project_root = output_dir.resolve() if output_dir else (
        source.parent / f"{source.stem}_expanded"
    )
    project_root.mkdir(parents=True, exist_ok=True)
    project = Project(project_root)
    project.ensure_dirs()

    stored_path = save_source_upload(project, source, source.name)
    console.print(f"[bold]Expanding: {source.name}[/bold]")
    console.print(f"  Project: {project.root}")

    config = project.config()

    try:
        if plan_only:
            document_text, sections = load_source_document_for_expansion(project)
            current_words = sum(s.word_count for s in sections if s.id != "preamble")

            # Resolve target
            if target_words and target_words > current_words:
                final_target = target_words
                final_multiplier = target_words / max(current_words, 1)
            elif multiplier and multiplier > 1.0:
                final_target = int(current_words * multiplier)
                final_multiplier = multiplier
            else:
                final_target = current_words * 2
                final_multiplier = 2.0

            console.print(
                f"  {len(sections)} sections, {current_words:,} -> "
                f"{final_target:,} target ({final_multiplier:.2f}x)\n"
            )
            console.print("[cyan]Planning expansion (Opus)...[/cyan]")

            refs = extract_all_refs(project.refs_dir, cache_dir=project.extracted_dir) \
                if project.refs_dir.exists() else {}

            plan = asyncio.run(run_expansion_planner(
                project, config, sections, document_text,
                final_target, final_multiplier, ref_texts=refs,
            ))

            console.print(
                f"\n[green]Plan saved[/green]: {len(plan.sections)} sections, "
                f"{sum(len(s.targets) for s in plan.sections)} expansion targets.\n"
            )
            console.print(f"  Plan JSON:    {project.expansion_plan_path}")
            console.print(f"  Plan summary: {project.expansion_plan_summary_path}")
            if plan.overall_strategy:
                console.print(f"\n  [bold]Strategy:[/bold] {plan.overall_strategy}")
            return

        console.print(
            f"  Config: parallelism={config.parallelism}, "
            f"planner={config.reviewer_model_id}, "
            f"expander={config.executor_model_id}"
        )
        if target_words:
            console.print(f"  Target: {target_words:,} words")
        elif multiplier:
            console.print(f"  Multiplier: {multiplier:.2f}x")
        else:
            console.print("  Target: 2x (default)")
        console.print(
            "\n[cyan]Running expansion pipeline "
            "(parse -> plan -> expand -> smooth)...[/cyan]\n"
        )

        summary = asyncio.run(run_expansion_pipeline(
            project=project,
            config=config,
            target_words=target_words,
            multiplier=multiplier,
            run_smoother=not no_smooth,
        ))

        console.print(
            f"[green]Expansion complete in {_fmt_duration(summary['duration_s'])}[/green]"
        )
        console.print(
            f"  Words: {summary['original_words']:,} -> {summary['expanded_words']:,} "
            f"(target {summary['target_words']:,}, achieved {summary['achieved_multiplier']:.2f}x)"
        )
        console.print(
            f"  Sections expanded: {summary['sections_expanded']}/{summary['sections_total']}"
        )
        if summary['sections_failed']:
            console.print(
                f"  [yellow]Failed sections: {summary['sections_failed']} "
                f"(originals kept)[/yellow]"
            )
        console.print(f"\n  Expanded:    {summary['expanded_path']}")
        console.print(f"  Plan (JSON): {summary['plan_path']}")
        console.print(f"  Plan (MD):   {summary['plan_summary_path']}")

    except ExpansionInputError as e:
        console.print(f"[red]Cannot expand: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:  # noqa: BLE001 -- show a readable message
        console.print(f"[red]Expansion pipeline crashed:[/red] {type(e).__name__}: {e}")
        console.print(
            f"[yellow]Per-section expansions under {project.expansions_dir} are preserved.[/yellow]"
        )
        console.print(
            "Run with --plan-only to produce a plan without expanding, or retry; "
            "if the plan exists, it will be reused and only the expand + smooth stages re-run."
        )
        raise typer.Exit(1)


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs:02d}s"
