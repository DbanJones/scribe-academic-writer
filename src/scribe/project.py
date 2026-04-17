"""Project directory abstraction and scaffolding."""

from __future__ import annotations

import shutil
from importlib import resources as pkg_resources
from pathlib import Path

from scribe.config import ScribeConfig
from scribe.models import Chunk


class Project:
    """Encapsulates all path resolution for a Scribe project."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    # --- Input paths ---

    @property
    def outline_path(self) -> Path:
        return self.root / "outline.md"

    @property
    def style_path(self) -> Path:
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
        if self.style_path.exists():
            return self.style_path.read_text(encoding="utf-8")
        default = pkg_resources.files("scribe.resources").joinpath("default_style.md")
        return default.read_text(encoding="utf-8")

    def load_writing_rules(self) -> str:
        """Load the academic writing rules (always included as quality baseline)."""
        rules = pkg_resources.files("scribe.resources").joinpath(
            "academic_writing_rules.md"
        )
        return rules.read_text(encoding="utf-8")

    def load_outline(self) -> str:
        if not self.outline_path.exists():
            raise FileNotFoundError(f"No outline.md found at {self.outline_path}")
        return self.outline_path.read_text(encoding="utf-8")

    def list_refs(self) -> list[Path]:
        if not self.refs_dir.exists():
            return []
        return sorted(
            p for p in self.refs_dir.rglob("*") if p.is_file()
        )

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
