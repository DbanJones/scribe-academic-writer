"""Split an existing document (markdown) into heading-delimited sections."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CITATION_RE = re.compile(r"\(([A-Z][A-Za-z\-'\u2019]+(?:\s+(?:et\s+al\.|&|and)\s+[A-Z][A-Za-z\-'\u2019]+)?(?:,\s*[A-Z][A-Za-z\-'\u2019]+)*,?\s*\d{4}[a-z]?(?:,\s*p{1,2}\.?\s*\d+[\-\u2013\u2014\d]*)?)\)")
FIGURE_RE = re.compile(r"(?i)(?:^|\s)(figure\s+\d+|table\s+\d+)")


@dataclass
class DocumentSection:
    """A section of a document, identified by its heading."""

    id: str              # stable id like "s1", "s2_3"
    title: str           # heading text
    level: int           # heading level (1-6). 0 for pre-heading content.
    order: int           # original order in document
    text: str            # full section text including heading line
    body: str            # section text without the heading line
    word_count: int = 0
    citations: list[str] = field(default_factory=list)  # citation strings found
    figures: list[str] = field(default_factory=list)    # "Figure 1", "Table 2" references
    heading_path: list[str] = field(default_factory=list)  # parent headings

    @property
    def filename(self) -> str:
        """Slug suitable for a draft filename."""
        slug = re.sub(r"[^\w\s-]", "", self.title.lower())
        slug = re.sub(r"[\s-]+", "_", slug).strip("_")[:40]
        return f"{self.id}_{slug}.md" if slug else f"{self.id}.md"


def split_into_sections(text: str) -> list[DocumentSection]:
    """Split a markdown document into sections delimited by headings.

    Each section contains the heading line, all bullets, paragraphs,
    and tables that follow it until the next heading at the same or
    shallower level.

    Content before the first heading is captured as a level-0 section
    with id "preamble" if it's non-trivial.
    """
    lines = text.splitlines()
    sections: list[DocumentSection] = []

    current_lines: list[str] = []
    current_heading: str | None = None
    current_level: int = 0
    current_start_idx: int = 0

    heading_path: list[tuple[int, str]] = []  # (level, title) stack

    def _flush(next_start: int) -> None:
        """Flush the current buffered section."""
        nonlocal current_lines, current_heading, current_level

        if not current_lines and current_heading is None:
            return

        section_text = "\n".join(current_lines).strip()
        if not section_text and current_heading is None:
            return

        # Build section
        if current_heading is None:
            # Preamble content (before any heading)
            section_id = "preamble"
            title = "(Preamble)"
            body = section_text
            level = 0
        else:
            section_id = f"s{len(sections) + 1}"
            title = current_heading
            # Body is everything after the heading line
            lines_without_heading = current_lines[1:] if current_lines else []
            body = "\n".join(lines_without_heading).strip()
            level = current_level

        # Compute path (parents at shallower levels)
        path = [t for (lvl, t) in heading_path if lvl < current_level and t != current_heading]

        citations = sorted({m.group(1) for m in CITATION_RE.finditer(section_text)})
        figures = sorted({m.group(1).strip() for m in FIGURE_RE.finditer(section_text)})

        sections.append(
            DocumentSection(
                id=section_id,
                title=title,
                level=level,
                order=len(sections),
                text=section_text,
                body=body,
                word_count=len(body.split()),
                citations=citations,
                figures=figures,
                heading_path=path,
            )
        )

        current_lines = []

    for idx, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if match:
            # Flush previous section
            _flush(idx)

            level = len(match.group(1))
            title = match.group(2).strip()

            # Update heading path: pop anything at same or deeper level
            while heading_path and heading_path[-1][0] >= level:
                heading_path.pop()
            heading_path.append((level, title))

            current_heading = title
            current_level = level
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush(len(lines))

    # Drop empty or trivial preamble
    sections = [s for s in sections if s.word_count > 0 or s.title != "(Preamble)"]

    return sections


def looks_like_substantive_draft(text: str, *, min_words: int = 1500) -> bool:
    """Heuristic: does this look like an existing draft (vs a sketch outline)?

    Criteria:
    - At least min_words total
    - At least one heading
    - At least some citations OR prose paragraphs (not all bullets)
    """
    words = len(text.split())
    if words < min_words:
        return False

    has_heading = any(HEADING_RE.match(line) for line in text.splitlines())
    if not has_heading:
        return False

    # Count non-bullet, non-empty lines (prose paragraphs)
    prose_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "#", "|")):
            continue
        if len(stripped.split()) >= 10:  # a real prose sentence
            prose_lines += 1

    has_citations = bool(CITATION_RE.search(text))
    return prose_lines >= 5 or has_citations


DEFAULT_SCAFFOLD_MARKERS = (
    "# My Document",
    "## Introduction",
    "## Body",
    "## Conclusion",
    "- Opening context",
    "- Thesis statement",
    "- Main argument",
    "- Supporting evidence",
    "- Summary of findings",
    "- Future directions",
)


def is_default_scaffold(text: str) -> bool:
    """Detect whether the outline is still the untouched default scaffold.

    The ``scribe init`` scaffold includes all of the markers listed in
    DEFAULT_SCAFFOLD_MARKERS. If 8+ of them are present verbatim, the
    user almost certainly has not written a real outline yet.
    """
    hits = sum(1 for marker in DEFAULT_SCAFFOLD_MARKERS if marker in text)
    return hits >= 8
