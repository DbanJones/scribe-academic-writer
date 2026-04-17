"""Diff detection — hash inputs and determine which chunks need regeneration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scribe.models import Plan
from scribe.project import Project


def compute_hashes(project: Project) -> dict[str, str]:
    """Compute SHA256 hashes for all input files."""
    hashes: dict[str, str] = {}

    for path, key in [
        (project.outline_path, "outline.md"),
        (project.style_path, "style.md"),
    ]:
        if path.exists():
            hashes[key] = _hash_file(path)

    for ref_path in project.list_refs():
        rel = str(ref_path.relative_to(project.root))
        hashes[rel] = _hash_file(ref_path)

    return hashes


def load_cached_hashes(project: Project) -> dict[str, str]:
    """Load previously cached hashes."""
    cache_path = project.cache_dir / "hashes.json"
    if not cache_path.exists():
        return {}
    return json.loads(cache_path.read_text(encoding="utf-8"))


def save_hashes(project: Project, hashes: dict[str, str]) -> None:
    """Save current hashes to cache."""
    cache_path = project.cache_dir / "hashes.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def changed_files(project: Project) -> list[str]:
    """Compare current hashes to cached, return list of changed file keys."""
    current = compute_hashes(project)
    cached = load_cached_hashes(project)

    changes: list[str] = []
    for key, h in current.items():
        if cached.get(key) != h:
            changes.append(key)

    # Files removed since last run
    for key in cached:
        if key not in current:
            changes.append(key)

    return changes


def chunks_affected_by_changes(
    plan: Plan,
    changed_paths: list[str],
) -> list[str]:
    """Map changed input files to affected chunk IDs.

    - outline.md or style.md changed -> all chunks affected
    - a ref file changed -> only chunks referencing that ref
    """
    if not changed_paths:
        return []

    # Global changes affect all chunks
    if "outline.md" in changed_paths or "style.md" in changed_paths:
        return [c.id for c in plan.chunks]

    # Map ref changes to chunks
    affected: set[str] = set()
    for chunk in plan.chunks:
        for source in chunk.sources:
            # Normalise path separators for comparison
            source_norm = source.file.replace("\\", "/")
            for changed in changed_paths:
                changed_norm = changed.replace("\\", "/")
                if source_norm == changed_norm or source_norm.endswith(changed_norm):
                    affected.add(chunk.id)

    return list(affected)


def _hash_file(path: Path) -> str:
    """SHA256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()
