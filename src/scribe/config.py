"""Configuration loading and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class GitConfig:
    auto_commit: bool = True
    commit_template: str = "scribe: {stage} for {project_name}"


@dataclass
class ScribeConfig:
    project_name: str = "Untitled"
    default_depth: str = "standard"
    planner_model: str = "claude-opus-4-0"
    executor_model: str = "claude-sonnet-4-6"
    stitcher_model: str = "claude-opus-4-0"
    parallelism: int = 3
    citation_style: str = "harvard"
    suggest_visuals: bool = True
    git: GitConfig = field(default_factory=GitConfig)
    estimate_tokens: bool = True

    @classmethod
    def load(cls, config_path: Path | None) -> ScribeConfig:
        if config_path is None or not config_path.exists():
            return cls()

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        git_raw = raw.pop("git", {}) or {}
        cost_raw = raw.pop("cost_tracking", {}) or {}

        git_cfg = GitConfig(
            auto_commit=git_raw.get("auto_commit", True),
            commit_template=git_raw.get(
                "commit_template", "scribe: {stage} for {project_name}"
            ),
        )

        return cls(
            project_name=raw.get("project_name", "Untitled"),
            default_depth=raw.get("default_depth", "standard"),
            planner_model=raw.get("planner_model", "opus"),
            executor_model=raw.get("executor_model", "sonnet"),
            stitcher_model=raw.get("stitcher_model", "opus"),
            parallelism=raw.get("parallelism", 3),
            citation_style=raw.get("citation_style", "harvard"),
            suggest_visuals=raw.get("suggest_visuals", True),
            git=git_cfg,
            estimate_tokens=cost_raw.get("estimate_tokens", True),
        )

    def resolve_model(self, short_name: str) -> str:
        """Resolve short model names like 'opus' or 'sonnet' to full IDs."""
        mapping = {
            "opus": "claude-opus-4-0",
            "sonnet": "claude-sonnet-4-6",
            "haiku": "claude-haiku-4-5-20251001",
        }
        return mapping.get(short_name, short_name)

    @property
    def planner_model_id(self) -> str:
        return self.resolve_model(self.planner_model)

    @property
    def executor_model_id(self) -> str:
        return self.resolve_model(self.executor_model)

    @property
    def stitcher_model_id(self) -> str:
        return self.resolve_model(self.stitcher_model)
