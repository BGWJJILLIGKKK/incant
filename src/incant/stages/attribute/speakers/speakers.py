"""LLM speaker attribution + delivery (emotion) for every quotation. Needs characters.json (parse=characters).

Spans are still cut deterministically (spans.py). The model sees a passage with each quotation marked
⟦n⟧ and returns, per marker, a character id (constrained by the JSON grammar to the roster, or "unknown")
plus a short delivery description and intensity. It never writes book text.

Chapters are cut into windows of at most `max_quotes` quotations, each preceded by a few paragraphs of
unlabelled context; windows run in parallel (bounded by the LLM token budget).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Literal

from pydantic import BaseModel, Field, create_model

from ....llm import LLM, LLMError
from ....schemas import Character, CharacterList, Emotion, Span, SpanKind
from ...base import Context
from ..base import AttributeMethod
from ..spans import split_chapter

__all__ = ["SpeakersAttribute"]

UNKNOWN = "unknown"
_NEUTRAL = re.compile(r"^\s*(neutral|plain|normal|calm|none|flat|matter[- ]of[- ]fact)\s*$", re.I)


class SpeakersSettings(BaseModel):
    max_quotes: int = 25  # quotations labelled per request
    max_window_words: int = 2500
    context_paragraphs: int = 4  # unlabelled paragraphs shown before each window
    roster_top: int = 10  # always include the N most-mentioned characters, besides those in the chapter
    think: bool = False
    max_tokens: int = 4096
    max_tokens_thinking: int = 8192
    # Emotion -> IndexTTS emo_alpha: intensity * emotion_scale, capped; below min_intensity = no emotion.
    emotion_scale: float = 0.8
    max_alpha: float = 0.8
    min_intensity: float = 0.3


SYSTEM = """You label who speaks each quotation in a passage of a novel, for a multi-voice audiobook.
Quotations to label are marked ⟦n⟧. For EVERY marker, in order, give:
- cue: the evidence for the speaker, briefly: the dialogue tag ("tag: said his wife"), or "same paragraph as ⟦n⟧", or "reply to ⟦n⟧ (alternation)", or what the words reveal ("addresses Mr. Bennet").
- speaker: the id of the character (from CHARACTERS) who says those words aloud, or "unknown" if it cannot be determined or the speaker is not listed.
- emotion: a few words for how it is said, as an actor's direction (e.g. "teasing, amused", "sharp irritation", "quiet grief", "eager excitement"), or "neutral".
- intensity: 0.0 to 1.0, how strongly that emotion colours the delivery (0.2 slight, 0.5 clear, 0.8 strong).
Deciding the speaker:
- Dialogue tags name the speaker: "said Elizabeth", "cried her mother", "replied he". Resolve relations and pronouns to a character using the passage (e.g. "his wife").
- A quotation in the same paragraph as a tagged one is usually the same speaker, unless the text says otherwise.
- In untagged back-and-forth conversation, speakers usually alternate between the two people talking.
- What a quotation says (who it addresses, what it refers to) is evidence: people do not usually address themselves by name.
- Characters may be named by any alias listed.
Text marked [context] is earlier text for reference only; it has no markers."""


def render_passage(context: list[list[Span]], window: list[list[Span]]) -> tuple[str, dict[int, str]]:
    """Text with ⟦n⟧ before each dialogue span in the window; returns (text, marker -> span id)."""
    lines = ["[context] " + "".join(s.text for s in para) for para in context]
    markers: dict[int, str] = {}
    n = 0
    for para in window:
        parts = []
        for s in para:
            if s.kind is SpanKind.dialogue:
                n += 1
                markers[n] = s.id
                parts.append(f"⟦{n}⟧{s.text}")
            else:
                parts.append(s.text)
        lines.append("".join(parts))
    return "\n\n".join(lines), markers


def roster_for(chars: CharacterList, chapter_id: str, top: int) -> list[Character]:
    ranked = sorted(chars.characters, key=lambda c: -c.mentions)
    keep = {c.id for c in ranked[:top]} | {c.id for c in chars.characters if chapter_id in c.chapters}
    return [c for c in ranked if c.id in keep]


def roster_text(roster: list[Character]) -> str:
    lines = []
    for c in roster:
        al = f"; also {', '.join(c.aliases[:6])}" if c.aliases else ""
        desc = f" — {c.description[:100]}" if c.description else ""
        lines.append(f"{c.id}: {c.canonical_name} ({c.gender.value}{al}){desc}")
    return "\n".join(lines)


def label_schema(n: int, ids: list[str]) -> type[BaseModel]:
    speaker_t = Literal[tuple(ids + [UNKNOWN])]  # type: ignore[valid-type]
    marker_t = Literal[tuple(range(1, n + 1))]  # type: ignore[valid-type]
    Label = create_model("Label", id=(marker_t, ...), cue=(str, Field(max_length=80)), speaker=(speaker_t, ...),
                         emotion=(str, Field(max_length=40)), intensity=(float, Field(ge=0.0, le=1.0)))
    return create_model("Labels", quotes=(list[Label], Field(min_length=n, max_length=n)))


def windows(paras: list[list[Span]], max_quotes: int, max_words: int) -> list[tuple[int, int]]:
    """Paragraph index ranges [a, b) holding at most max_quotes quotations / max_words words."""
    out, start, q, w = [], 0, 0, 0
    for i, para in enumerate(paras):
        pq = sum(s.kind is SpanKind.dialogue for s in para)
        pw = sum(len(s.text.split()) for s in para)
        if i > start and (q + pq > max_quotes or w + pw > max_words):
            out.append((start, i))
            start, q, w = i, 0, 0
        q += pq
        w += pw
    if start < len(paras):
        out.append((start, len(paras)))
    return [(a, b) for a, b in out if any(s.kind is SpanKind.dialogue for p in paras[a:b] for s in p)]


class SpeakersAttribute(AttributeMethod):
    Settings = SpeakersSettings
    settings: SpeakersSettings

    def fingerprint(self) -> str:
        return super().fingerprint() + ":" + self.cfg.llm.model

    def check(self) -> list[tuple[bool, str]]:
        try:
            ok = self.cfg.llm.model in LLM(self.cfg.llm).models()
            return [(ok, f"llm model '{self.cfg.llm.model}' at {self.cfg.llm.base_url}")]
        except Exception as e:  # noqa: BLE001
            return [(False, f"llm endpoint {self.cfg.llm.base_url}: {e}")]

    def emotion(self, text: str, intensity: float) -> Emotion | None:
        if intensity < self.settings.min_intensity or not text.strip() or _NEUTRAL.match(text):
            return None
        return Emotion(text=text.strip(), alpha=round(min(self.settings.max_alpha, intensity * self.settings.emotion_scale), 2))

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        chars = wd.read_characters()
        if not chars or not chars.characters:
            raise RuntimeError("speakers needs characters.json — run the parse stage with the 'characters' method")
        llm = LLM(self.cfg.llm, cache_dir=wd.root / "llm_cache")

        book: dict[str, list[Span]] = {}
        jobs = []
        for ch in wd.read_chapters().chapters:
            if ctx.chapters is not None and ch.id not in ctx.chapters:
                if not wd.spans_path(ch.id).exists():  # keep other chapters' existing labels
                    wd.write_spans(ch.id, split_chapter(ch.id, wd.read_chapter_text(ch.file)))
                continue
            spans = split_chapter(ch.id, wd.read_chapter_text(ch.file))
            book[ch.id] = spans
            paras: list[list[Span]] = []
            for s in spans:
                if not paras or paras[-1][0].para != s.para:
                    paras.append([])
                paras[-1].append(s)
            roster = roster_for(chars, ch.id, self.settings.roster_top)
            for a, b in windows(paras, self.settings.max_quotes, self.settings.max_window_words):
                jobs.append((ch.id, paras[max(0, a - self.settings.context_paragraphs):a], paras[a:b], roster))

        labels: dict[str, tuple[str, Emotion | None]] = {}
        failed = 0
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.llm.concurrency)) as ex:
            futs = [ex.submit(self._label, llm, *j) for j in jobs]
            for done, f in enumerate(as_completed(futs), 1):
                try:
                    labels.update(f.result())
                except LLMError as e:
                    failed += 1
                    ctx.log(f"  window failed: {e}")
                if done % 20 == 0 or done == len(jobs):
                    ctx.log(f"  labelled {done}/{len(jobs)} windows")

        counts: dict[str, int] = {}
        total = unknown = 0
        for cid, spans in book.items():
            for s in spans:
                if s.kind is SpanKind.dialogue:
                    total += 1
                    who, emo = labels.get(s.id, (UNKNOWN, None))
                    s.speaker = None if who == UNKNOWN else who
                    s.emotion = emo
                    unknown += s.speaker is None
                    if s.speaker:
                        counts[s.speaker] = counts.get(s.speaker, 0) + 1
            wd.write_spans(cid, spans)

        if ctx.chapters is None:  # line counts drive casting priority; only meaningful for a full run
            for c in chars.characters:
                c.line_count = counts.get(c.id, 0)
            wd.write_characters(chars)
        ctx.log(f"  {total:,} quotations: {total - unknown:,} attributed, {unknown:,} unknown"
                + (f"; WARNING {failed} windows failed" if failed else ""))
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        ctx.log("  most lines: " + ", ".join(f"{k} {v}" for k, v in top))
        return counts

    def _label(self, llm: LLM, chapter: str, context, window, roster: list[Character]) -> dict[str, tuple[str, Emotion | None]]:
        text, markers = render_passage(context, window)
        ids = [c.id for c in roster]
        schema = label_schema(len(markers), ids)
        prompt = f"CHARACTERS:\n{roster_text(roster)}\n\nPASSAGE:\n{text}"
        s = self.settings
        r = llm.chat_json(SYSTEM, prompt, schema, think=s.think, max_tokens=s.max_tokens_thinking if s.think else s.max_tokens)
        out = {}
        for q in r.quotes:
            sid = markers.get(q.id)
            if sid and sid not in out:
                out[sid] = (q.speaker, self.emotion(q.emotion, q.intensity))
        return out


