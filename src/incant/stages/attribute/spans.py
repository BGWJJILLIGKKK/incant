"""Stage 3a: deterministic narration/dialogue span splitting.

This is code, not LLM, on purpose: the LLM (stage 3b) only labels spans by id,
so it can never paraphrase, drop, or invent text.

Handles curly “double” and straight "double" quotes, including multi-paragraph
quotes (unclosed at paragraph end). Not yet handled: ‘single-quote’ dialogue
(British style), em-dash dialogue, «guillemets».
"""

from __future__ import annotations

import re

from ...schemas import NARRATOR_ID, Span, SpanKind
from ...workdir import WorkDir

_OPEN = "“"
_CLOSE = "”"
_STRAIGHT = '"'
_SPEAKABLE = re.compile(r"[A-Za-z0-9]")


def split_paragraph(p: str) -> list[tuple[SpanKind, str]]:
    parts: list[tuple[SpanKind, str]] = []
    buf: list[str] = []
    in_quote = False

    def flush(kind: SpanKind):
        if buf:
            parts.append((kind, "".join(buf)))
            buf.clear()

    for ch in p:
        if not in_quote and ch in (_OPEN, _STRAIGHT):
            flush(SpanKind.narration)
            buf.append(ch)
            in_quote = True
        elif in_quote and ch in (_CLOSE, _STRAIGHT):
            buf.append(ch)
            flush(SpanKind.dialogue)
            in_quote = False
        elif in_quote and ch == _OPEN:  # unbalanced: new quote opens before the old one closed
            flush(SpanKind.dialogue)
            buf.append(ch)
        else:
            buf.append(ch)
    flush(SpanKind.dialogue if in_quote else SpanKind.narration)

    # Fold unspeakable narration (", " / " — " / whitespace) into a neighbour so
    # the text invariant holds without creating empty TTS calls.
    merged: list[tuple[SpanKind, str]] = []
    pending_prefix = ""
    for kind, text in parts:
        if kind is SpanKind.narration and not _SPEAKABLE.search(text):
            if merged:
                merged[-1] = (merged[-1][0], merged[-1][1] + text)
            else:
                pending_prefix += text
            continue
        merged.append((kind, pending_prefix + text))
        pending_prefix = ""
    if pending_prefix:
        merged.append((SpanKind.narration, pending_prefix))
    return merged


def split_chapter(chapter_id: str, text: str) -> list[Span]:
    spans: list[Span] = []
    n = 0
    for para_idx, p in enumerate(text.rstrip("\n").split("\n\n")):
        for kind, t in split_paragraph(p):
            n += 1
            spans.append(
                Span(
                    id=f"{chapter_id}-{n:05d}",
                    chapter_id=chapter_id,
                    para=para_idx,
                    kind=kind,
                    text=t,
                    speaker=NARRATOR_ID if kind is SpanKind.narration else None,
                )
            )
    return spans


def reconstruct(spans: list[Span]) -> str:
    paras: dict[int, list[str]] = {}
    for s in spans:
        paras.setdefault(s.para, []).append(s.text)
    return "\n\n".join("".join(v) for _, v in sorted(paras.items()))


def keep_locked(wd: WorkDir, chapter_id: str, spans: list[Span]) -> int:
    """Carry hand-reviewed labels (locked spans, e.g. from the review page) over to freshly built spans,
    matched by id and unchanged text. Returns how many were kept."""
    if not wd.spans_path(chapter_id).exists():
        return 0
    old = {s.id: s for s in wd.read_spans(chapter_id) if s.locked}
    kept = 0
    for s in spans:
        o = old.get(s.id)
        if o and o.text == s.text:
            s.speaker, s.emotion, s.locked = o.speaker, o.emotion, True
            kept += 1
    return kept


def build_spans(wd: WorkDir, force: bool = False) -> dict[str, int]:
    """Write spans/<id>.jsonl for every chapter. Existing files are kept unless force,
    because they may hold LLM attribution or hand edits."""
    counts: dict[str, int] = {}
    for ch in wd.read_chapters().chapters:
        path = wd.spans_path(ch.id)
        if path.exists() and not force:
            counts[ch.id] = -1
            continue
        text = wd.read_chapter_text(ch.file)
        spans = split_chapter(ch.id, text)
        assert reconstruct(spans) == text.rstrip("\n"), f"span invariant broken in chapter {ch.id}"
        keep_locked(wd, ch.id, spans)
        wd.write_spans(ch.id, spans)
        counts[ch.id] = len(spans)
    return counts
