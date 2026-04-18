"""Flask web frontend for Scribe."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from scribe.config import ScribeConfig
from scribe.models import Plan
from scribe.project import Project

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = "scribe-web-session-key"

# In-memory state for tracking runs
_run_state: dict = {
    "status": "idle",  # idle, planning, reviewing, writing, stitching, done, error
    "message": "",
    "progress": 0,
    "chunks_done": 0,
    "chunks_total": 0,
    "error": None,
    "final_path": None,
    "final_words": 0,
    "log": [],
}
_run_lock = threading.Lock()


def _update_state(**kwargs):
    with _run_lock:
        _run_state.update(kwargs)


def _add_log(msg: str):
    with _run_lock:
        _run_state["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        # Keep last 100 log entries
        if len(_run_state["log"]) > 100:
            _run_state["log"] = _run_state["log"][-100:]


def _get_builtin_styles() -> list[dict]:
    """List available built-in style guides."""
    from importlib import resources as pkg_resources

    styles = []
    resources_dir = pkg_resources.files("scribe.resources")

    default_style = resources_dir.joinpath("default_style.md")
    styles.append({
        "name": "Default (British English, Academic)",
        "filename": "default_style.md",
        "description": "British English, concise active voice, Harvard citations, no em dashes",
    })

    return styles


@app.route("/")
def index():
    styles = _get_builtin_styles()
    return render_template("index.html", builtin_styles=styles, run_state=_run_state)


@app.route("/create", methods=["POST"])
def create_project():
    """Create a new Scribe project from uploaded files."""
    output_folder = request.form.get("output_folder", "").strip()
    if not output_folder:
        flash("Output folder is required.", "error")
        return redirect(url_for("index"))

    output_path = Path(output_folder)

    # Create or load project
    project = Project.scaffold(output_path)

    # Handle outline upload
    outline_file = request.files.get("outline")
    if outline_file and outline_file.filename:
        ext = Path(outline_file.filename).suffix.lower() or ".md"
        dest = project.root / f"outline{ext}"
        outline_file.save(str(dest))
        _add_log(f"Uploaded outline: {outline_file.filename}")

    # Handle style guide
    style_choice = request.form.get("style_choice", "builtin")
    if style_choice == "upload":
        style_file = request.files.get("style_guide")
        if style_file and style_file.filename:
            ext = Path(style_file.filename).suffix.lower() or ".md"
            dest = project.root / f"style{ext}"
            style_file.save(str(dest))
            _add_log(f"Uploaded style guide: {style_file.filename}")
    elif style_choice == "builtin":
        # Copy default style if not already present
        if not project.style_path.exists():
            from importlib import resources as pkg_resources

            default = pkg_resources.files("scribe.resources").joinpath("default_style.md")
            shutil.copy2(str(default), project.style_path)
            _add_log("Using built-in default style guide")

    # Handle reference files
    ref_files = request.files.getlist("refs")
    for ref_file in ref_files:
        if ref_file and ref_file.filename:
            dest = project.refs_dir / ref_file.filename
            ref_file.save(str(dest))
            _add_log(f"Uploaded ref: {ref_file.filename}")

    # Handle config
    project_name = request.form.get("project_name", "Untitled").strip()
    depth = request.form.get("default_depth", "standard")
    parallelism = int(request.form.get("parallelism", "3"))
    suggest_visuals = request.form.get("suggest_visuals") == "on"

    config_data = {
        "project_name": project_name,
        "default_depth": depth,
        "planner_model": "opus",
        "executor_model": "sonnet",
        "stitcher_model": "opus",
        "parallelism": parallelism,
        "suggest_visuals": suggest_visuals,
        "git": {"auto_commit": False},
    }

    import yaml

    project.config_path.write_text(
        yaml.dump(config_data, default_flow_style=False),
        encoding="utf-8",
    )
    _add_log(f"Project configured: {project_name}")

    flash(f"Project created at {output_path}", "success")
    return redirect(url_for("project_view", project_dir=str(output_path)))


@app.route("/project")
def project_view():
    """View and manage an existing project."""
    project_dir = request.args.get("project_dir", "")
    if not project_dir:
        flash("No project directory specified.", "error")
        return redirect(url_for("index"))

    project_path = Path(project_dir)
    project = Project(project_path)
    if not project.outline_path.exists():
        flash(f"No outline found in {project_dir}", "error")
        return redirect(url_for("index"))

    config = project.config()

    # Gather project info
    info = {
        "root": str(project.root),
        "project_name": config.project_name,
        "outline_exists": project.outline_path.exists(),
        "style_exists": project.style_path.exists(),
        "ref_count": len(project.list_refs()),
        "refs": [p.name for p in project.list_refs()],
        "plan_exists": project.plan_path.exists(),
        "final_exists": project.final_path.exists(),
        "review_exists": project.review_path.exists(),
    }

    plan = None
    if project.plan_path.exists():
        try:
            plan = Plan.load(project.plan_path)
        except Exception:
            pass

    doc_review = None
    if project.review_path.exists():
        try:
            from scribe.models import DocumentReview
            doc_review = DocumentReview.load(project.review_path)
        except Exception:
            pass

    final_words = 0
    if project.final_path.exists():
        final_words = len(project.final_path.read_text(encoding="utf-8").split())

    return render_template(
        "project.html",
        info=info,
        plan=plan,
        doc_review=doc_review,
        final_words=final_words,
        run_state=_run_state,
    )


@app.route("/run", methods=["POST"])
def run_pipeline():
    """Start the full pipeline in a background thread."""
    project_dir = request.form.get("project_dir", "")
    skip_review = request.form.get("skip_review") == "on"

    if _run_state["status"] not in ("idle", "done", "error"):
        return jsonify({"error": "A run is already in progress"}), 409

    _update_state(
        status="planning",
        message="Starting pipeline...",
        progress=0,
        chunks_done=0,
        chunks_total=0,
        error=None,
        final_path=None,
        final_words=0,
        log=[],
    )

    thread = threading.Thread(
        target=_run_pipeline_thread,
        args=(project_dir, skip_review),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("project_view", project_dir=project_dir))


def _run_pipeline_thread(project_dir: str, skip_review: bool):
    """Run the full Scribe pipeline in a background thread."""
    try:
        project = Project(Path(project_dir))
        config = project.config()
        project.ensure_dirs()

        # --- DOCUMENT REVIEW ---
        _update_state(status="reviewing", message="Analysing thesis structure (Opus)...")
        _add_log("Stage 1/4: Document thesis review...")

        from scribe.stages.reviewer import run_reviewer

        doc_review = asyncio.run(run_reviewer(project, config))
        review_context = doc_review.as_prompt_context()
        _add_log(f"  Key question: {doc_review.key_question}")
        _add_log(f"  Themes: {', '.join(doc_review.key_themes)}")
        _add_log(f"  Problem: {doc_review.problem_statement[:100]}")

        # Build section mappings for per-chunk role context
        section_mappings = {}
        for m in doc_review.section_mappings:
            section_mappings[m.section] = {
                "role": m.role,
                "answers_question_by": m.answers_question_by,
            }

        _update_state(progress=15)

        # --- PLAN ---
        _update_state(status="planning", message="Running planner (Opus)...")
        _add_log("Stage 2/4: Planning...")

        from scribe.stages.planner import run_planner

        plan = asyncio.run(run_planner(project, config, review_context=review_context))
        _add_log(f"Plan: {plan.estimated_chunks} chunks, ~{plan.estimated_words:,} words")

        if plan.gaps:
            for g in plan.gaps:
                _add_log(f"  Gap: {g.bullet} -- {g.issue}")

        _update_state(
            status="writing",
            message=f"Writing {len(plan.chunks)} chunks...",
            chunks_total=len(plan.chunks),
            progress=30,
        )

        # --- WRITE ---
        _add_log(f"Stage 3/4: Writing {len(plan.chunks)} chunks in parallel...")

        from scribe.stages.executor import run_executor

        async def _on_event(chunk_id, event_type, data):
            if event_type == "result" and not data.get("is_error"):
                with _run_lock:
                    _run_state["chunks_done"] += 1
                    done = _run_state["chunks_done"]
                    total = _run_state["chunks_total"]
                    _run_state["progress"] = 30 + int(50 * done / max(total, 1))
                _add_log(f"  Chunk {chunk_id} done")

        drafts = asyncio.run(
            run_executor(
                project, plan, config,
                stream_callback=_on_event,
                force=True,
                review_context=review_context,
                section_mappings=section_mappings,
            )
        )
        _add_log(f"{len(drafts)} drafts written")

        # --- STITCH ---
        _update_state(status="stitching", message="Stitching final document (Opus)...", progress=83)
        _add_log("Stage 4/4: Stitching...")

        from scribe.stages.stitcher import run_stitcher

        final_path = asyncio.run(
            run_stitcher(project, plan, config, review_context=review_context)
        )
        final_words = len(final_path.read_text(encoding="utf-8").split())

        _update_state(
            status="done",
            message=f"Done! {final_words:,} words",
            progress=100,
            final_path=str(final_path),
            final_words=final_words,
        )
        _add_log(f"Final document: {final_words:,} words at {final_path}")

    except Exception as e:
        _update_state(status="error", message=str(e)[:300], error=str(e))
        _add_log(f"ERROR: {e}")


@app.route("/status")
def get_status():
    """API endpoint for polling run status."""
    with _run_lock:
        return jsonify(dict(_run_state))


@app.route("/upload_refs", methods=["POST"])
def upload_refs():
    """Upload additional reference files to an existing project."""
    project_dir = request.form.get("project_dir", "")
    project = Project(Path(project_dir))

    ref_files = request.files.getlist("refs")
    count = 0
    for ref_file in ref_files:
        if ref_file and ref_file.filename:
            dest = project.refs_dir / ref_file.filename
            ref_file.save(str(dest))
            count += 1

    flash(f"{count} reference file(s) uploaded.", "success")
    return redirect(url_for("project_view", project_dir=project_dir))


def run_web(host: str = "127.0.0.1", port: int = 5000, debug: bool = False):
    """Start the web server."""
    app.run(host=host, port=port, debug=debug)
