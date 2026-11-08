"""Turn span text into TTS-ready chunks.

Currently: strip markup and quote marks, collapse whitespace, and split into
sentence-packed chunks under a character budget. Number/abbreviation expansion is
in speech.py, and the lexicon is applied by the normalize stage.
"""

from __future__ import annotations

import re

from .normalize import is_scene_break

_ABBREV = r"(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Prof|Capt|Col|Gen|Lt|Sgt|Rev|Hon|vs|etc|No|Mt|Ft)"
_SENT_END = re.compile(r"[.!?…][\"”’)\]]*(?=\s+[\"“‘(\[]?[A-Z0-9])")
_ENDS_WITH_ABBREV = re.compile(rf"\b{_ABBREV}\.$")
_SOFT_BREAK = re.compile(r"(?<=[;:,—–])\s+")


def speakable(text: str) -> str:
    t = text.replace("*", "")
    t = re.sub(r"[“”\"]", "", t)
    t = t.replace("\\#", "#")
    t = re.sub(r"\s+", " ", t).strip()
    return t if re.search(r"[A-Za-z0-9]", t) else ""


def _sentences(t: str) -> list[str]:
    out, start = [], 0
    for m in _SENT_END.finditer(t):
        if _ENDS_WITH_ABBREV.search(t[: m.start() + 1]):
            continue
        out.append(t[start : m.end()].strip())
        start = m.end()
    out.append(t[start:].strip())
    return [s for s in out if s]


def _hard_split(s: str, max_chars: int) -> list[str]:
    """Split one over-long sentence at clause boundaries, then at spaces."""
    out, cur = [], ""
    for piece in _SOFT_BREAK.split(s):
        if cur and len(cur) + 1 + len(piece) > max_chars:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    final = []
    for c in out:
        while len(c) > max_chars:
            cut = c.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            final.append(c[:cut].strip())
            c = c[cut:].strip()
        if c:
            final.append(c)
    return final


def chunk(text: str, max_chars: int) -> list[str]:
    t = speakable(text)
    if not t:
        return []
    sentences = _sentences(t)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(s) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_hard_split(s, max_chars))
        elif cur and len(cur) + 1 + len(s) > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


__all__ = ["chunk", "speakable", "is_scene_break"]
