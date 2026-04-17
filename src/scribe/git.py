"""Git integration using GitPython."""

from __future__ import annotations

import logging
from pathlib import Path

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError

logger = logging.getLogger(__name__)


class ScribeRepo:
    """Wraps GitPython for Scribe's version history needs."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self._repo: Repo | None = None

    @property
    def repo(self) -> Repo | None:
        if self._repo is None:
            try:
                self._repo = Repo(str(self.root))
            except InvalidGitRepositoryError:
                return None
        return self._repo

    def is_initialised(self) -> bool:
        return self.repo is not None

    def init(self) -> Repo:
        """Initialise a git repo if not already done."""
        if self.repo:
            return self.repo

        self._repo = Repo.init(str(self.root))

        # Create .gitignore if needed
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "refs/\n.scribe/cache/\n__pycache__/\n",
                encoding="utf-8",
            )

        # Initial commit
        self._repo.index.add([".gitignore"])
        self._repo.index.commit("scribe: init")
        return self._repo

    def commit_stage(
        self, stage: str, project_name: str, template: str | None = None
    ) -> str | None:
        """Stage and commit all Scribe-managed files.

        Returns commit hash or None if nothing to commit.
        """
        if not self.repo:
            logger.debug("No git repo, skipping commit")
            return None

        # Add all relevant files
        paths_to_add = []
        for pattern in [
            ".scribe/plan.json",
            ".scribe/plan_review.md",
            ".scribe/plan_history/*",
            ".scribe/drafts/*",
            ".scribe/runs/*",
            ".scribe/state.json",
            "final.md",
            "outline.md",
            "style.md",
            ".gitignore",
        ]:
            from glob import glob

            matched = glob(str(self.root / pattern), recursive=True)
            for m in matched:
                rel = str(Path(m).relative_to(self.root))
                paths_to_add.append(rel)

        if not paths_to_add:
            logger.debug("No files to commit")
            return None

        try:
            self.repo.index.add(paths_to_add)
        except Exception as e:
            logger.warning("Git add failed: %s", e)
            return None

        # Check if there are staged changes
        if not self.repo.index.diff("HEAD"):
            # Also check for untracked files that were just added
            if not self.repo.untracked_files:
                logger.debug("Nothing to commit")
                return None

        msg = (template or "scribe: {stage} for {project_name}").format(
            stage=stage, project_name=project_name
        )

        try:
            commit = self.repo.index.commit(msg)
            logger.info("Committed: %s (%s)", msg, commit.hexsha[:7])
            return commit.hexsha
        except Exception as e:
            logger.warning("Git commit failed: %s", e)
            return None

    def tag_run(self, run_id: str) -> bool:
        """Create a git tag for the run."""
        if not self.repo:
            return False

        tag_name = f"run-{run_id.replace('_', '-')}"
        try:
            self.repo.create_tag(tag_name)
            logger.info("Tagged: %s", tag_name)
            return True
        except GitCommandError:
            logger.warning("Tag %s already exists", tag_name)
            return False

    def history(self, limit: int = 20) -> list[dict]:
        """Return recent scribe commits."""
        if not self.repo:
            return []

        results = []
        for commit in self.repo.iter_commits(max_count=limit * 3):
            if commit.message.startswith("scribe:"):
                results.append({
                    "hash": commit.hexsha[:7],
                    "message": commit.message.strip(),
                    "date": commit.committed_datetime.isoformat(),
                    "files": len(commit.stats.files),
                })
                if len(results) >= limit:
                    break

        return results

    def diff_runs(self, run1_tag: str, run2_tag: str) -> str:
        """Show diff of final.md between two run tags."""
        if not self.repo:
            return "(No git repo)"

        try:
            return self.repo.git.diff(
                f"run-{run1_tag}", f"run-{run2_tag}",
                "--", "final.md",
            )
        except GitCommandError as e:
            return f"(Git diff failed: {e})"
