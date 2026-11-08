"""Project Gutenberg plain-text books: `txt` plus the Gutenberg cleanup — the license header and footer
around "*** START/END OF THE PROJECT GUTENBERG EBOOK … ***" and [Illustration: …] placeholders (keeping a
chapter heading Gutenberg sometimes wraps inside one). Picked automatically for .txt files with those markers.
"""

from __future__ import annotations

import re

from ..txt.txt import TxtIngest

__all__ = ["GutenbergIngest", "strip_gutenberg"]

_START = re.compile(r"^\*{3}\s*START OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_END = re.compile(r"^\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_ILLUSTRATION = re.compile(r"\[Illustration[^\]]*\]", re.I | re.S)
_CAPTION_HEADING = re.compile(r"^\s*((chapter|book|part|volume)\s+[\w.]+\.?)\s*$", re.I)
SNIFF_BYTES = 64 * 1024  # the START marker comes after the header block, well within this


def strip_gutenberg(s: str) -> str:
    """Remove the license header/footer and illustration placeholders."""
    if m := _START.search(s):
        s = s[m.end():]
    if m := _END.search(s):
        s = s[: m.start()]
    return _ILLUSTRATION.sub(_keep_heading, s)


def _keep_heading(m: re.Match) -> str:
    """'[Illustration: …\\n\\nChapter I.]' -> keep the heading, drop the caption."""
    lines = [ln for ln in m.group(0)[1:-1].splitlines() if ln.strip()]
    if lines and (h := _CAPTION_HEADING.match(lines[-1])):
        return f"\n\n{h.group(1)}\n\n"
    return ""


class GutenbergIngest(TxtIngest):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'
    @classmethod
    def accepts(cls, source: str) -> bool:
        if not super().accepts(source):
            return False
        with open(source, "rb") as f:
            head = f.read(SNIFF_BYTES).decode("utf-8", errors="replace")
        return bool(_START.search(head))

    def clean(self, raw: str) -> str:
        return strip_gutenberg(super().clean(raw))
