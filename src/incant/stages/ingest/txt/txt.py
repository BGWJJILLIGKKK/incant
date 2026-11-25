"""Plain text or Markdown. "Title:" / "Author:" header lines, if any, name the book.
Subclasses (e.g. `gutenberg`) override clean() to remove source-specific boilerplate."""

from __future__ import annotations

import re
from pathlib import Path

from ....normalize import clean_unicode, heading, paragraphs_from_plain
from ..base import Ingested, IngestMethod

__all__ = ["TxtIngest"]


def _header_field(raw: str, name: str) -> str | None:
    m = re.search(rf"^{name}:\s*(.+)$", raw[:5000], re.M | re.I)
    return m.group(1).strip() if m else None


_MARKDOWN_HEADING = re.compile(r" {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_MARKDOWN_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")


def markdown_paragraphs(raw: str) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    fence = ""
    for line in raw.splitlines():
        marker = _MARKDOWN_FENCE.fullmatch(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
        elif marker:
            fence = marker[1]
        elif match := _MARKDOWN_HEADING.fullmatch(line):
            paragraphs.extend(paragraphs_from_plain("\n".join(buffer)))
            paragraphs.append(heading(match[1]))
            buffer.clear()
            continue
        buffer.append(line)
    return paragraphs + paragraphs_from_plain("\n".join(buffer))


class TxtIngest(IngestMethod):
    """Plain text or Markdown."""
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() in (".txt", ".md", ".markdown", "")

    def clean(self, raw: str) -> str:
        """The text to split into paragraphs; header fields are read before this."""
        return raw

    def load(self, source: str) -> Ingested:
        path = Path(source)
        raw = clean_unicode(path.read_text(encoding="utf-8-sig", errors="replace"))
        title = _header_field(raw, "Title") or path.stem.replace("_", " ").replace("-", " ").title()
        fmt = "md" if path.suffix.lower() in (".md", ".markdown") else "txt"
        read = markdown_paragraphs if fmt == "md" else paragraphs_from_plain
        return Ingested(title=title, author=_header_field(raw, "Author"),
                        paragraphs=read(self.clean(raw)), source_format=fmt)
