"""Parse outline.md into structured sections with inline tag extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


TAG_PATTERN = re.compile(r"\[(\w+)(?::([^\]]*))?\]")


@dataclass
class OutlineTag:
    tag_type: str  # "depth", "ref", "web", "skip", "words"
    value: str | int | None = None


@dataclass
class OutlineBullet:
    text: str
    level: int  # nesting depth (0 = top-level bullet)
    tags: list[OutlineTag] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)


@dataclass
class OutlineSection:
    heading: str
    level: int  # h1=1, h2=2, etc.
    bullets: list[OutlineBullet] = field(default_factory=list)
    tags: list[OutlineTag] = field(default_factory=list)
    subsections: list[OutlineSection] = field(default_factory=list)


def extract_tags(line: str) -> tuple[str, list[OutlineTag]]:
    """Extract inline tags from a line, return (clean_line, tags)."""
    tags: list[OutlineTag] = []
    for match in TAG_PATTERN.finditer(line):
        tag_type = match.group(1).lower()
        raw_value = match.group(2)

        if tag_type == "words" and raw_value is not None:
            try:
                value: str | int | None = int(raw_value)
            except ValueError:
                value = raw_value
        elif tag_type in ("web", "skip"):
            value = None
        else:
            value = raw_value

        tags.append(OutlineTag(tag_type=tag_type, value=value))

    clean = TAG_PATTERN.sub("", line).strip()
    return clean, tags


def _heading_level(line: str) -> int | None:
    """Return heading level (1-6) or None if not a heading."""
    match = re.match(r"^(#{1,6})\s+", line)
    return len(match.group(1)) if match else None


def _bullet_level(line: str) -> int | None:
    """Return bullet nesting level (0+) or None if not a bullet."""
    match = re.match(r"^(\s*)[-*+]\s+", line)
    if not match:
        return None
    indent = len(match.group(1))
    return indent // 2  # 2 spaces per indent level


def parse_outline(text: str) -> list[OutlineSection]:
    """Parse outline markdown into structured sections."""
    lines = text.splitlines()
    root_sections: list[OutlineSection] = []
    section_stack: list[OutlineSection] = []
    heading_path: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        h_level = _heading_level(stripped)
        if h_level is not None:
            heading_text = re.sub(r"^#{1,6}\s+", "", stripped)
            clean_heading, tags = extract_tags(heading_text)

            section = OutlineSection(
                heading=clean_heading, level=h_level, tags=tags
            )

            # Unwind stack to find parent
            while section_stack and section_stack[-1].level >= h_level:
                section_stack.pop()
                if heading_path:
                    heading_path.pop()

            if section_stack:
                section_stack[-1].subsections.append(section)
            else:
                root_sections.append(section)

            section_stack.append(section)
            heading_path.append(clean_heading)
            continue

        b_level = _bullet_level(stripped)
        if b_level is not None:
            bullet_text = re.sub(r"^\s*[-*+]\s+", "", stripped)
            clean_text, tags = extract_tags(bullet_text)

            bullet = OutlineBullet(
                text=clean_text,
                level=b_level,
                tags=tags,
                heading_path=list(heading_path),
            )

            if section_stack:
                section_stack[-1].bullets.append(bullet)

    return root_sections


def section_text_for_chunk(
    sections: list[OutlineSection], covers: list[str]
) -> str:
    """Given chunk.covers (list of section/heading names), extract relevant outline text."""
    covers_lower = {c.lower() for c in covers}
    parts: list[str] = []

    def _walk(section: OutlineSection) -> None:
        if section.heading.lower() in covers_lower:
            parts.append(f"## {section.heading}")
            for bullet in section.bullets:
                indent = "  " * bullet.level
                parts.append(f"{indent}- {bullet.text}")
            for sub in section.subsections:
                _walk_all(sub)
        else:
            for sub in section.subsections:
                _walk(sub)

    def _walk_all(section: OutlineSection) -> None:
        parts.append(f"{'#' * section.level} {section.heading}")
        for bullet in section.bullets:
            indent = "  " * bullet.level
            parts.append(f"{indent}- {bullet.text}")
        for sub in section.subsections:
            _walk_all(sub)

    for s in sections:
        _walk(s)

    return "\n".join(parts)
