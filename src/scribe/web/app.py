"""Flask web frontend for Scribe."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
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
    send_file,
    url_for,
)

from scribe import auth as scribe_auth
from scribe import recent as scribe_recent
from scribe.config import ScribeConfig
from scribe.models import Plan
from scribe.project import Project
from scribe.web import picker as scribe_picker

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = "scribe-web-session-key"


@app.context_processor
def _inject_auth_status():
    """Make auth info available to every template (for the masthead badge)."""
    try:
        return {"auth_status": scribe_auth.check_login()}
    except Exception:
        return {"auth_status": {"installed": False, "logged_in": False}}


@app.template_filter("time_ago")
def _time_ago(ts):
    """Render a Unix timestamp as a relative 'n minutes ago' string."""
    try:
        delta = time.time() - float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m}m ago"
    if delta < 86400:
        h = int(delta // 3600)
        return f"{h}h ago"
    if delta < 2592000:
        d = int(delta // 86400)
        return f"{d}d ago"
    mo = int(delta // 2592000)
    return f"{mo}mo ago"


def _require_login_json():
    """Return a 401 JSON response if the user is not logged in, else None."""
    status = scribe_auth.check_login()
    if not status.get("logged_in"):
        return (
            jsonify({
                "error": "Not logged in. Visit /login and click 'Login with Claude'.",
                "redirect": "/login",
            }),
            401,
        )
    return None

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


# (path_str, mtime_ns) -> DocumentReview. Bounded by review-file identity so
# a fresh review invalidates automatically.
_DOC_REVIEW_CACHE: dict[tuple[str, int], "object"] = {}


def _load_doc_review_cached(path: Path):
    from scribe.models import DocumentReview
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return DocumentReview.load(path)
    key = (str(path), mtime)
    cached = _DOC_REVIEW_CACHE.get(key)
    if cached is not None:
        return cached
    review = DocumentReview.load(path)
    _DOC_REVIEW_CACHE.clear()  # Keep the cache tiny; we only ever need the latest.
    _DOC_REVIEW_CACHE[key] = review
    return review


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
    """Dashboard: recent projects + CTAs."""
    recents = scribe_recent.load()
    return render_template("dashboard.html", recents=recents, active_nav="dashboard")


@app.route("/new")
def new_project_view():
    styles = _get_builtin_styles()
    return render_template(
        "new_project.html",
        builtin_styles=styles,
        run_state=_run_state,
        active_nav="new",
    )


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
    scribe_recent.touch(output_path, name=request.form.get("project_name") or None)

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

    # Record this project in the recent list so dashboard stays up to date.
    scribe_recent.touch(project_path)

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
            doc_review = _load_doc_review_cached(project.review_path)
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
    gate = _require_login_json()
    if gate is not None:
        return gate

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


# ---------------------------------------------------------------------------
# Restyle / citation-engine routes
# ---------------------------------------------------------------------------

_restyle_state: dict = {
    "status": "idle",  # idle, extracting, polishing, done, error
    "message": "",
    "progress": 0,
    "log": [],
    "output_path": None,
    "in_text_count": 0,
    "bibliography_count": 0,
    "error": None,
}
_restyle_lock = threading.Lock()


def _rs_update(**kwargs):
    with _restyle_lock:
        _restyle_state.update(kwargs)


def _rs_log(msg: str):
    with _restyle_lock:
        _restyle_state["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if len(_restyle_state["log"]) > 120:
            _restyle_state["log"] = _restyle_state["log"][-120:]


@app.route("/restyle")
def restyle_view():
    from scribe.citations import SCHEMA_FIELDS, list_styles

    styles = list_styles()
    return render_template(
        "restyle.html",
        styles=styles,
        schema=SCHEMA_FIELDS,
        run_state=_restyle_state,
    )


@app.route("/styles")
def styles_view():
    """Alias: the Styles nav link just takes you to the restyle page."""
    return redirect(url_for("restyle_view"))


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Render a sample citation in the chosen style with optional overrides."""
    from scribe.citations import load_style, render_bibliography_entry, render_in_text
    from scribe.citations.engine import _deep_merge, diff_against_parent

    data = request.get_json() or {}
    style_id = data.get("style_id", "harvard")
    overrides = data.get("overrides", {}) or {}
    sample = data.get("sample") or {}
    sample_book = data.get("sample_book") or {}

    if style_id == "__custom__":
        # Build an ad-hoc style from overrides on top of harvard as a sensible default.
        base = load_style("harvard")
    else:
        try:
            base = load_style(style_id)
        except FileNotFoundError:
            return jsonify({"error": f"Unknown style: {style_id}"}), 400

    override_tree: dict = {}
    for dotted, value in overrides.items():
        cur = override_tree
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        # Coerce booleans/numbers
        if isinstance(value, str):
            if value.isdigit():
                value = int(value)
            elif value.lower() in ("true", "false"):
                value = value.lower() == "true"
        cur[parts[-1]] = value

    style = _deep_merge(base, override_tree)
    style["styleName"] = base.get("styleName", style_id)

    try:
        intext = render_in_text(sample, style, num=12)
        narrative = render_in_text(sample, style, num=12, narrative=True)
        bib_journal = render_bibliography_entry(sample, style) if sample else ""
        bib_book = render_bibliography_entry(sample_book, style) if sample_book else ""
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    diffs = []
    if style_id != "__custom__":
        diffs = diff_against_parent(style_id)

    return jsonify({
        "intext": intext,
        "narrative": narrative,
        "bib_journal": bib_journal,
        "bib_book": bib_book,
        "style_name": style.get("styleName"),
        "style_description": style.get("styleDescription", ""),
        "parent": style.get("parent"),
        "diffs": diffs,
    })


@app.route("/api/save_preset", methods=["POST"])
def api_save_preset():
    from scribe.citations.schema import build_custom_style, save_custom_style

    data = request.get_json() or {}
    style_id = (data.get("style_id") or "").strip()
    if not style_id:
        return jsonify({"error": "Preset id is required"}), 400

    try:
        style_data = build_custom_style(
            style_id=style_id,
            style_name=data.get("style_name") or style_id,
            parent=data.get("parent") or None,
            form_values=data.get("overrides") or {},
            additional_rules=data.get("additional_rules") or "",
        )
        path = save_custom_style(style_data)
    except (ValueError, OSError) as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"style_id": style_data["styleId"], "path": str(path)})


@app.route("/restyle_run", methods=["POST"])
def restyle_run():
    gate = _require_login_json()
    if gate is not None:
        return gate

    paper = request.files.get("paper")
    paper_path = (request.form.get("paper_path") or "").strip()
    if not paper_path and (not paper or not paper.filename):
        return "No paper provided", 400
    if paper_path and not Path(paper_path).is_file():
        return f"Path does not exist or is not a file: {paper_path}", 400

    style_id = request.form.get("style", "harvard")
    model_short = request.form.get("model", "opus")
    skip_polish = request.form.get("skip_polish") == "on"
    output_name = (request.form.get("output_name") or "").strip()
    additional_rules = request.form.get("additional_rules", "")

    # If style is "__custom__", auto-save under a timestamped id so the engine can load it.
    if style_id == "__custom__":
        from scribe.citations.schema import build_custom_style, save_custom_style

        custom_id = request.form.get("custom_id") or f"tmp-{int(time.time())}"
        overrides = {
            k.replace("override__", ""): v
            for k, v in request.form.items()
            if k.startswith("override__") and v
        }
        style_data = build_custom_style(
            style_id=custom_id,
            style_name=request.form.get("custom_name") or custom_id,
            parent=request.form.get("custom_parent") or "harvard",
            form_values=overrides,
            additional_rules=additional_rules,
        )
        save_custom_style(style_data)
        style_id = style_data["styleId"]

    if _restyle_state["status"] in ("extracting", "polishing"):
        return jsonify({"error": "A restyle is already in progress"}), 409

    # Resolve the input. Server-side path wins over upload (faster, no copy).
    if paper_path:
        input_path = Path(paper_path).expanduser().resolve()
        tmp_dir = input_path.parent
        source_label = str(input_path)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="scribe-restyle-"))
        suffix = Path(paper.filename).suffix.lower() or ".md"
        input_path = tmp_dir / f"input{suffix}"
        paper.save(str(input_path))
        source_label = paper.filename

    output_path = None
    if output_name:
        output_path = tmp_dir / output_name

    _rs_update(
        status="extracting",
        message="Extracting citations...",
        progress=5,
        log=[],
        output_path=None,
        in_text_count=0,
        bibliography_count=0,
        error=None,
    )
    _rs_log(f"Input: {source_label}")
    _rs_log(f"Target style: {style_id}")

    thread = threading.Thread(
        target=_restyle_thread,
        args=(input_path, style_id, model_short, output_path, additional_rules, skip_polish),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "started"})


def _restyle_thread(
    input_path: Path,
    style_id: str,
    model_short: str,
    output_path: Path | None,
    additional_rules: str,
    skip_polish: bool,
):
    try:
        from scribe.citations.restyler import restyle_document
        cfg = ScribeConfig()
        model_id = cfg.resolve_model(model_short)

        async def _progress(event: str, payload: dict):
            if event == "document_read":
                _rs_update(progress=15, message="Document read, calling Claude to extract citations...")
                _rs_log(f"Document read: {payload.get('chars', 0)} chars ({payload.get('kind')})")
            elif event == "citations_extracted":
                _rs_update(progress=55, status="polishing",
                           message=f"{payload.get('in_text')} citations, {payload.get('references')} refs extracted.")
                _rs_log(f"Extraction done: {payload.get('in_text')} in-text, {payload.get('references')} references")
            elif event == "bibliography_built":
                _rs_update(progress=70, message=f"Rebuilt {payload.get('count')} references.")
                _rs_log(f"Bibliography rebuilt: {payload.get('count')} entries")
            elif event == "polishing":
                _rs_update(progress=80, status="polishing", message="Polishing formatting rules...")
                _rs_log("Polish pass started")
            elif event == "done":
                _rs_log(f"Output: {payload.get('output')}")

        result = asyncio.run(restyle_document(
            input_path,
            style_id=style_id,
            output_path=output_path,
            extra_rules=additional_rules,
            model=model_id,
            skip_polish=skip_polish,
            progress=_progress,
        ))
        _rs_update(
            status="done",
            progress=100,
            message=f"Done! {result.in_text_count} citations, {result.bibliography_count} references.",
            output_path=str(result.output_path),
            in_text_count=result.in_text_count,
            bibliography_count=result.bibliography_count,
        )
        _rs_log(f"Finished in {result.duration_s:.1f}s, cost ${result.cost_usd:.4f}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        _rs_update(status="error", error=str(e), message=str(e)[:300])
        _rs_log(f"ERROR: {e}")


@app.route("/restyle_status")
def restyle_status():
    with _restyle_lock:
        return jsonify(dict(_restyle_state))


@app.route("/restyle_download")
def restyle_download():
    with _restyle_lock:
        path = _restyle_state.get("output_path")
    if not path or not Path(path).exists():
        return "No output available yet", 404
    return send_file(path, as_attachment=True)


# ---------------------------------------------------------------------------
# Revision routes
# ---------------------------------------------------------------------------

_revise_state: dict = {
    "status": "idle",  # idle, parsing, auditing, revising, stitching, done, error
    "message": "",
    "progress": 0,
    "log": [],
    "project_dir": None,
    "revised_path": None,
    "audit_summary_path": None,
    "original_words": 0,
    "revised_words": 0,
    "sections_total": 0,
    "sections_done": 0,
    "error": None,
}
_revise_lock = threading.Lock()


def _rv_update(**kwargs):
    with _revise_lock:
        _revise_state.update(kwargs)


def _rv_log(msg: str):
    with _revise_lock:
        _revise_state["log"].append(
            f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        )
        if len(_revise_state["log"]) > 200:
            _revise_state["log"] = _revise_state["log"][-200:]


@app.route("/revise")
def revise_view():
    """Landing page: upload a draft for revision."""
    with _revise_lock:
        state = dict(_revise_state)
    return render_template("revise.html", active_nav="revise", revise_state=state)


@app.route("/revise_start", methods=["POST"])
def revise_start():
    """Accept uploaded source + output folder, kick off the revision pipeline."""
    login = _require_login_json()
    if login:
        return login

    if _revise_state["status"] not in ("idle", "done", "error"):
        flash("A revision is already in progress.", "error")
        return redirect(url_for("revise_view"))

    source_file = request.files.get("source")
    if not source_file or not source_file.filename:
        flash("Please select a document to revise.", "error")
        return redirect(url_for("revise_view"))

    output_folder = request.form.get("output_folder", "").strip()
    if not output_folder:
        flash("Please specify an output folder.", "error")
        return redirect(url_for("revise_view"))

    project_root = Path(output_folder).resolve()
    project_root.mkdir(parents=True, exist_ok=True)
    project = Project(project_root)
    project.ensure_dirs()

    # Save uploaded source into the project
    from scribe.revision import save_source_upload

    tmp_dir = Path(tempfile.mkdtemp(prefix="scribe_revise_"))
    tmp_path = tmp_dir / source_file.filename
    source_file.save(str(tmp_path))
    stored = save_source_upload(project, tmp_path, source_file.filename)
    tmp_path.unlink(missing_ok=True)
    tmp_dir.rmdir()

    # Also copy an optional custom style guide if provided
    style_file = request.files.get("style_guide")
    if style_file and style_file.filename:
        ext = Path(style_file.filename).suffix.lower() or ".md"
        style_dest = project.root / f"style{ext}"
        style_file.save(str(style_dest))

    _rv_update(
        status="parsing",
        message="Starting revision...",
        progress=0,
        log=[],
        project_dir=str(project.root),
        revised_path=None,
        audit_summary_path=None,
        original_words=0,
        revised_words=0,
        sections_total=0,
        sections_done=0,
        error=None,
    )
    _rv_log(f"Uploaded source: {source_file.filename} -> {stored.name}")
    scribe_recent.record_project(project.root, config=project.config())

    thread = threading.Thread(
        target=_run_revise_thread,
        args=(str(project.root),),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("revise_view"))


def _run_revise_thread(project_dir: str):
    """Execute the revision pipeline in a background thread."""
    try:
        from scribe.parsers.sections import split_into_sections
        from scribe.project import _read_document
        from scribe.revision import (
            RevisionInputError,
            load_source_document,
            run_revision_pipeline,
        )
        from scribe.stages.auditor import run_auditor
        from scribe.stages.reviser import (
            assemble_revised_document,
            run_reviser,
        )
        from scribe.stages.revision_stitcher import run_revision_stitcher

        project = Project(Path(project_dir))
        config = project.config()

        # Stage 1: parse
        _rv_update(status="parsing", message="Parsing source document...", progress=5)
        try:
            document_text, sections = load_source_document(project)
        except RevisionInputError as e:
            _rv_update(status="error", message=str(e), error=str(e), progress=0)
            _rv_log(f"ERROR: {e}")
            return

        _rv_update(
            original_words=sum(s.word_count for s in sections),
            sections_total=len(sections),
        )
        _rv_log(f"Parsed: {len(sections)} sections, {sum(s.word_count for s in sections):,} words")

        # Stage 2: audit
        _rv_update(status="auditing", message="Auditing against academic rules (Opus)...",
                   progress=15)
        _rv_log("Stage 2/4: Running auditor...")

        async def _audit_cb(cid, event_type, data):
            if event_type == "tool_use":
                name = data.get("name", "")
                _rv_log(f"  auditor: {name}")

        audit = asyncio.run(run_auditor(
            project, config, sections, document_text,
            stream_callback=_audit_cb,
        ))
        _rv_log(
            f"Audit: {len(audit.sections)} sections graded, "
            f"{len(audit.overall_issues)} document-level issues"
        )
        if audit.overall_verdict:
            _rv_log(f"Verdict: {audit.overall_verdict[:200]}")

        _rv_update(
            progress=30,
            audit_summary_path=str(project.audit_summary_path),
        )

        # Build overall context (section outline)
        from scribe.revision import _build_overall_context
        overall_context = _build_overall_context(sections)

        # Stage 3: revise
        _rv_update(status="revising", message=f"Revising {len(sections)} sections (Sonnet, parallel)...",
                   progress=35)
        _rv_log(f"Stage 3/4: Revising sections (parallelism={config.parallelism})...")

        async def _revise_cb(cid, event_type, data):
            if event_type == "result" and not data.get("is_error"):
                with _revise_lock:
                    _revise_state["sections_done"] += 1
                    done = _revise_state["sections_done"]
                    total = _revise_state["sections_total"] or 1
                    _revise_state["progress"] = 35 + int(45 * done / total)
                _rv_log(f"  revised: {cid}")

        results = asyncio.run(run_reviser(
            project=project,
            config=config,
            audit=audit,
            sections=sections,
            overall_context=overall_context,
            stream_callback=_revise_cb,
        ))
        failed = sum(1 for r in results if r.is_error)
        _rv_log(f"Revision done: {len(results) - failed}/{len(results)} sections revised"
                + (f" ({failed} failed, originals kept)" if failed else ""))

        # Stage 4: stitch
        _rv_update(status="stitching", message="Smoothing transitions (Opus)...",
                   progress=82)
        _rv_log("Stage 4/4: Smoothing transitions...")

        assembled = assemble_revised_document(results)
        revised_path = asyncio.run(run_revision_stitcher(
            project, config, assembled,
        ))

        revised_words = len(revised_path.read_text(encoding="utf-8").split())
        _rv_update(
            status="done",
            message=f"Done! {revised_words:,} words",
            progress=100,
            revised_path=str(revised_path),
            revised_words=revised_words,
        )
        _rv_log(f"Revised document: {revised_words:,} words at {revised_path}")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger = __import__("logging").getLogger(__name__)
        logger.exception("Revision pipeline failed")
        _rv_update(status="error", message=str(e)[:300], error=tb[-1500:])
        _rv_log(f"ERROR: {e}")


@app.route("/revise_status")
def revise_status():
    with _revise_lock:
        return jsonify(dict(_revise_state))


@app.route("/revise_download")
def revise_download():
    with _revise_lock:
        path = _revise_state.get("revised_path")
    if not path or not Path(path).exists():
        return "No output available yet", 404
    return send_file(path, as_attachment=True)


@app.route("/revise_download_audit")
def revise_download_audit():
    with _revise_lock:
        path = _revise_state.get("audit_summary_path")
    if not path or not Path(path).exists():
        return "No audit available yet", 404
    return send_file(path, as_attachment=True)


# ---------------------------------------------------------------------------
# Claude CLI auth routes
# ---------------------------------------------------------------------------

@app.route("/login")
def login_view():
    """Login page: shows CLI status, login button, and streaming output."""
    status = scribe_auth.check_login(force=True)
    return render_template("login.html", auth=status, active_nav="login")


@app.route("/api/auth/status")
def api_auth_status():
    force = request.args.get("refresh") == "1"
    return jsonify(scribe_auth.check_login(force=force))


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    return jsonify(scribe_auth.start_login())


@app.route("/api/auth/login_poll")
def api_auth_login_poll():
    return jsonify(scribe_auth.poll_login())


@app.route("/api/auth/cancel", methods=["POST"])
def api_auth_cancel():
    return jsonify(scribe_auth.cancel_login())


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    return jsonify(scribe_auth.logout())


# ---------------------------------------------------------------------------
# Native OS picker routes (folder + file)
# ---------------------------------------------------------------------------

@app.route("/api/pick_folder", methods=["POST"])
def api_pick_folder():
    data = request.get_json(silent=True) or {}
    path = scribe_picker.pick_folder(
        title=data.get("title") or "Choose a folder",
        initial=data.get("initial"),
    )
    if path is None:
        return jsonify({"cancelled": True})
    return jsonify({"path": path})


@app.route("/api/pick_file", methods=["POST"])
def api_pick_file():
    data = request.get_json(silent=True) or {}
    filetypes = [tuple(t) for t in (data.get("filetypes") or [])]
    path = scribe_picker.pick_file(
        title=data.get("title") or "Choose a file",
        initial=data.get("initial"),
        filetypes=filetypes or None,
    )
    if path is None:
        return jsonify({"cancelled": True})
    return jsonify({"path": path})


@app.route("/api/recent/remove", methods=["POST"])
def api_recent_remove():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if path:
        scribe_recent.remove(path)
    return jsonify({"ok": True})


def run_web(host: str = "127.0.0.1", port: int = 5000, debug: bool = False):
    """Start the web server."""
    app.run(host=host, port=port, debug=debug)
