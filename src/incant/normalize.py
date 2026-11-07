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



def clean_unicode(s: str) -> str:
    s = unicodedata.normalize("NFC", s).translate(_LIGATURES)
    return s.replace("\r\n", "\n").replace("\r", "\n")


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
