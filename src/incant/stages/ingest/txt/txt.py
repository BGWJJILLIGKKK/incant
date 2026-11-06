from __future__ import annotations

import re
from pathlib import Path

from ....normalize import clean_unicode, paragraphs_from_plain, strip_gutenberg
from ..base import Ingested, IngestMethod

__all__ = ["TxtIngest"]


def _header_field(raw: str, name: str) -> str | None:
    m = re.search(rf"^{name}:\s*(.+)$", raw[:5000], re.M | re.I)
    return m.group(1).strip() if m else None


class TxtIngest(IngestMethod):
    """Plain text or Markdown. Strips Project Gutenberg boilerplate."""

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() in (".txt", ".md", ".markdown", "")

    def load(self, source: str) -> Ingested:
        return ingest_txt(Path(source))


def ingest_txt(path: Path) -> Ingested:
    raw = clean_unicode(path.read_text(encoding="utf-8-sig", errors="replace"))
    title = _header_field(raw, "Title") or path.stem.replace("_", " ").replace("-", " ").title()
    author = _header_field(raw, "Author")
    body = strip_gutenberg(raw)
    fmt = "md" if path.suffix.lower() in (".md", ".markdown") else "txt"
    return Ingested(title=title, author=author, paragraphs=paragraphs_from_plain(body), source_format=fmt)
