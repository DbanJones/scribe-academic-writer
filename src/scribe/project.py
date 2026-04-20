"""Project directory abstraction and scaffolding."""

from __future__ import annotations

import shutil
from importlib import resources as pkg_resources
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import Chunk


def _read_document(path: Path) -> str:
    """Read a document file, converting DOCX/DOC to markdown text."""
    suffix = path.suffix.lower()

    if suffix in (".md", ".txt", ""):
        return path.read_text(encoding="utf-8")

    if suffix == ".docx":
        from scribe.parsers.docx import extract
        return extract(path)

    if suffix == ".doc":
        # .doc (legacy Word) -- try converting via docx parser with a warning
        # python-docx doesn't support .doc, so we attempt textract or raw read
        try:
            from scribe.parsers.docx import extract
            return extract(path)
        except Exception:
            # Fallback: try reading as plain text
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                raise ValueError(
                    f"Cannot read .doc file: {path}. "
                    "Please convert to .docx or .md format."
                )

    return path.read_text(encoding="utf-8")


class Project:
    """Encapsulates all path resolution for a Scribe project."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        # Per-instance memo for list_refs. The cache key is the refs_dir
        # mtime_ns: if anyone adds/removes a file the directory mtime bumps
        # and we re-scan.
        self._refs_cache: tuple[int, list[Path]] | None = None

    # --- Input paths ---

    OUTLINE_EXTENSIONS = (".md", ".txt", ".docx", ".doc")
    STYLE_EXTENSIONS = (".md", ".txt", ".docx", ".doc")

    @property
    def outline_path(self) -> Path:
        """Find the outline file, checking multiple extensions."""
        for ext in self.OUTLINE_EXTENSIONS:
            p = self.root / f"outline{ext}"
            if p.exists():
                return p
        return self.root / "outline.md"  # default even if missing

    @property
    def style_path(self) -> Path:
        """Find the style guide file, checking multiple extensions."""
        for ext in self.STYLE_EXTENSIONS:
            p = self.root / f"style{ext}"
            if p.exists():
                return p
        return self.root / "style.md"

    @property
    def refs_dir(self) -> Path:
        return self.root / "refs"

    @property
    def config_path(self) -> Path:
        return self.root / "config.yml"

    # --- .scribe paths ---

    @property
    def scribe_dir(self) -> Path:
        return self.root / ".scribe"

    @property
    def review_path(self) -> Path:
        return self.scribe_dir / "document_review.json"

    @property
    def review_summary_path(self) -> Path:
        return self.scribe_dir / "document_review.md"

    # --- Revision mode paths ---

    # --- Expansion mode paths ---

    @property
    def expansion_plan_path(self) -> Path:
        return self.scribe_dir / "expansion_plan.json"

    @property
    def expansion_plan_summary_path(self) -> Path:
        return self.scribe_dir / "expansion_plan.md"

    @property
    def expansions_dir(self) -> Path:
        return self.scribe_dir / "expansions"

    @property
    def expanded_path(self) -> Path:
        return self.root / "expanded.md"

    @property
    def audit_path(self) -> Path:
        return self.scribe_dir / "audit.json"

    @property
    def audit_summary_path(self) -> Path:
        return self.scribe_dir / "audit.md"

    @property
    def revisions_dir(self) -> Path:
        return self.scribe_dir / "revisions"

    @property
    def revised_path(self) -> Path:
        return self.root / "revised.md"

    @property
    def source_document_path(self) -> Path:
        """Where an uploaded source document is stored for revision mode."""
        # Preferred location; actual extension handled at upload time.
        return self.root / "source.md"

    @property
    def plan_path(self) -> Path:
        return self.scribe_dir / "plan.json"

    @property
    def plan_review_path(self) -> Path:
        return self.scribe_dir / "plan_review.md"

    @property
    def plan_history_dir(self) -> Path:
        return self.scribe_dir / "plan_history"

    @property
    def drafts_dir(self) -> Path:
        return self.scribe_dir / "drafts"

    @property
    def state_path(self) -> Path:
        return self.scribe_dir / "state.json"

    @property
    def cache_dir(self) -> Path:
        return self.scribe_dir / "cache"

    @property
    def extracted_dir(self) -> Path:
        return self.cache_dir / "extracted"

    @property
    def runs_dir(self) -> Path:
        return self.scribe_dir / "runs"

    @property
    def final_path(self) -> Path:
        return self.root / "final.md"

    # --- Helpers ---

    def config(self) -> ScribeConfig:
        return ScribeConfig.load(self.config_path)

    def load_style(self) -> str:
        path = self.style_path
        if path.exists():
            return _read_document(path)
        default = pkg_resources.files("scribe.resources").joinpath("default_style.md")
        return default.read_text(encoding="utf-8")

    def load_writing_rules(self) -> str:
        """Load the academic writing rules (always included as quality baseline)."""
        rules = pkg_resources.files("scribe.resources").joinpath(
            "academic_writing_rules.md"
        )
        return rules.read_text(encoding="utf-8")

    def load_outline(self) -> str:
        path = self.outline_path
        if not path.exists():
            raise FileNotFoundError(
                f"No outline found at {self.root}. "
                f"Expected one of: {', '.join('outline' + e for e in self.OUTLINE_EXTENSIONS)}"
            )
        return _read_document(path)

    def list_refs(self) -> list[Path]:
        if not self.refs_dir.exists():
            self._refs_cache = None
            return []
        try:
            mtime = self.refs_dir.stat().st_mtime_ns
        except OSError:
            mtime = 0

        if self._refs_cache and self._refs_cache[0] == mtime:
            return list(self._refs_cache[1])

        refs = sorted(p for p in self.refs_dir.rglob("*") if p.is_file())
        self._refs_cache = (mtime, refs)
        return list(refs)

    def draft_path(self, chunk: Chunk) -> Path:
        slug = chunk.title.lower().replace(" ", "_")[:30]
        return self.drafts_dir / f"{chunk.id}_{slug}.md"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def ensure_dirs(self) -> None:
        for d in [
            self.scribe_dir,
            self.plan_history_dir,
            self.drafts_dir,
            self.cache_dir,
            self.extracted_dir,
            self.runs_dir,
            self.revisions_dir,
            self.expansions_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # --- Scaffolding ---

    @staticmethod
    def scaffold(root: Path) -> Project:
        root.mkdir(parents=True, exist_ok=True)
        (root / "refs").mkdir(exist_ok=True)

        project = Project(root)
        project.ensure_dirs()

        # Default style
        if not project.style_path.exists():
            default = pkg_resources.files("scribe.resources").joinpath(
                "default_style.md"
            )
            shutil.copy2(str(default), project.style_path)

        # Example outline
        if not project.outline_path.exists():
            project.outline_path.write_text(
                "# My Document\n\n"
                "## Introduction\n"
                "- Opening context\n"
                "- Thesis statement\n\n"
                "## Body\n"
                "- Main argument [depth:deep]\n"
                "- Supporting evidence\n\n"
                "## Conclusion\n"
                "- Summary of findings\n"
                "- Future directions\n",
                encoding="utf-8",
            )

        # .gitignore
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "refs/\n"
                ".scribe/cache/\n"
                "__pycache__/\n",
                encoding="utf-8",
            )

        return project
