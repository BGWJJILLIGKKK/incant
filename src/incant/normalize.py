"""Text normalization shared by all ingesters.

Output convention for text.md:
  * paragraphs are single lines separated by exactly one blank line
  * '# Title' paragraphs are structural headings (chapter boundaries)
  * '* * *' paragraphs are scene breaks
  * *asterisks* mark emphasis (kept for later stages; stripped before TTS)
  * curly quotes / dashes are preserved — dialogue detection depends on them
"""

from __future__ import annotations

import re
import unicodedata

SCENE_BREAK = "* * *"
_SCENE_BREAK_RE = re.compile(r"^\s*([*#~•·=_-]\s*){3,}$|^\s*[⁂§]\s*$")
_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", " ": " ", "​": ""})

_GUTENBERG_START = re.compile(r"^\*{3}\s*START OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_GUTENBERG_END = re.compile(r"^\*{3}\s*END OF (THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
_ILLUSTRATION = re.compile(r"\[Illustration[^\]]*\]", re.I | re.S)


def clean_unicode(s: str) -> str:
    s = unicodedata.normalize("NFC", s).translate(_LIGATURES)
    return s.replace("\r\n", "\n").replace("\r", "\n")


def strip_gutenberg(s: str) -> str:
    """Remove Project Gutenberg license header/footer and illustration placeholders."""
    if m := _GUTENBERG_START.search(s):
        s = s[m.end():]
    if m := _GUTENBERG_END.search(s):
        s = s[: m.start()]
    return _ILLUSTRATION.sub(_illustration_keep_heading, s)


_CAPTION_HEADING = re.compile(r"^\s*((chapter|book|part|volume)\s+[\w.]+\.?)\s*$", re.I)


def _illustration_keep_heading(m: re.Match) -> str:
    """Gutenberg sometimes wraps the chapter heading inside the illustration block:
    '[Illustration: ...\n\nChapter I.]'. Keep the heading, drop the caption."""
    lines = [ln for ln in m.group(0)[1:-1].splitlines() if ln.strip()]
    if lines and (h := _CAPTION_HEADING.match(lines[-1])):
        return f"\n\n{h.group(1)}\n\n"
    return ""


def is_scene_break(p: str) -> bool:
    return bool(_SCENE_BREAK_RE.match(p))


def clean_paragraph(p: str) -> str:
    p = re.sub(r"\s+", " ", p).strip()
    # Gutenberg uses _underscores_ for italics; unify on *asterisks*.
    p = re.sub(r"(?<![\w*])_([^_\n]+?)_(?![\w*])", r"*\1*", p)
    if p.startswith("# "):  # protect our heading marker from source text
        p = "\\" + p
    return p


def paragraphs_from_plain(text: str) -> list[str]:
    """Split hard-wrapped plain text into paragraphs (blank-line separated)."""
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for b in blocks:
        p = clean_paragraph(b)
        if not p:
            continue
        out.append(SCENE_BREAK if is_scene_break(p) else p)
    return out


def heading(title: str) -> str:
    return "# " + re.sub(r"\s+", " ", title).strip()


def render_text_md(paragraphs: list[str]) -> str:
    # collapse consecutive scene breaks, drop leading/trailing ones
    out: list[str] = []
    for p in paragraphs:
        if p == SCENE_BREAK and (not out or out[-1] == SCENE_BREAK or out[-1].startswith("# ")):
            continue
        out.append(p)
    while out and out[-1] == SCENE_BREAK:
        out.pop()
    return "\n\n".join(out) + "\n"


def slugify(s: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:max_len].rstrip("-") or "book"
