"""LLM speaker attribution + delivery (emotion) for every quotation. Needs characters.json (parse=characters).

Spans are still cut deterministically (spans.py). The model sees a passage with each quotation marked
⟦n⟧ and returns, per marker, a character id (constrained by the JSON grammar to the roster, "narrator",
"other" — an unnamed speaker by role — or "unknown")
plus a short delivery description and intensity. It never writes book text.

Chapters are cut into target windows surrounded by unlabelled context within the request budget.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import zip_longest
from typing import Literal

from pydantic import BaseModel, Field, create_model

from ....llm import LLM, LLMError, prompt_char_budget
from ....normalize import slugify
from ....schemas import NARRATOR_ID, Character, CharacterList, Emotion, Gender, Span, SpanKind
from ...base import Context
from ..base import AttributeMethod
from ..spans import keep_locked, split_chapter

__all__ = ["SpeakersAttribute"]

UNKNOWN = "unknown"
OTHER = "other"  # an unnamed speaker identified by role ("the charwoman"); becomes a minor character
UNNAMED = "unnamed"
_FIRST_PERSON = re.compile(r"\b(I|me|my|myself)\b")
_NEUTRAL = re.compile(r"^\s*(neutral|plain|normal|calm|none|flat|matter[- ]of[- ]fact)\s*$", re.I)


class SpeakersSettings(BaseModel):
    max_quotes: int = 25  # quotations labelled per request
    max_window_words: int = 2500
    context_paragraphs: int | None = Field(default=None, ge=0)  # limit on either side, None uses the token budget
    context_tokens: int = Field(default=262144, gt=0)
    narrator_chapters: int = Field(default=5, ge=1)
    roster_top: int = 10  # always include the N most-mentioned characters, besides those in the chapter
    # Books with at most this many speaking characters put all of them in every roster. Off by default:
    # extra names are distractors (P&P ch. I-II: 95.3% -> 87.5% with all 36). The text-based additions in
    # roster_for() cover characters the per-chapter data misses without that cost.
    full_roster_max: int = 0
    think: bool = False
    max_tokens: int = 4096
    max_tokens_thinking: int = 8192
    # Emotion -> IndexTTS emo_alpha: intensity * emotion_scale, capped; below min_intensity = no emotion.
    emotion_scale: float = 0.8
    max_alpha: float = 0.8
    min_intensity: float = 0.3
    # First-person detection: "I/me/my" per 1000 words of narration (third person ~1, first person ~25).
    first_person_rate: float = 8.0


SYSTEM = """You label who speaks each quotation in a passage of a novel, for a multi-voice audiobook.
Quotations to label are marked ⟦n⟧. For EVERY marker, in order, give:
- cue: the evidence for the speaker, briefly: the dialogue tag ("tag: said his wife"), or "same paragraph as ⟦n⟧", or "reply to ⟦n⟧ (alternation)", or what the words reveal ("addresses Mr. Bennet").
- speaker: the id of the character (from CHARACTERS) who says those words aloud. If the speaker is not in CHARACTERS but the passage identifies them by a role or description (a businessman, the charwoman, a boy in the street), use "other". Use "unknown" only when you cannot tell who speaks.
- speaker "narrator": only when nobody in the scene says the words aloud: written text (a letter, note, sign or book being described), a single quoted word or term, or a remark the text says was never made. Words a character says aloud — including play-acting, singing, reading aloud or thinking to themselves — belong to that character.
- other_role: only when speaker is "other": that person's role in a few words, the same wording every time they speak ("first businessman", "the charwoman"); otherwise "".
- other_gender: only when speaker is "other": "male", "female" or "unknown"; otherwise "unknown".
- emotion: a few words for how it is said, as an actor's direction (e.g. "teasing, amused", "sharp irritation", "quiet grief", "eager excitement"), or "neutral".
- intensity: 0.0 to 1.0, how strongly that emotion colours the delivery (0.2 slight, 0.5 clear, 0.8 strong).
Deciding the speaker:
- Dialogue tags name the speaker: "said Elizabeth", "cried her mother", "replied he". Resolve relations and pronouns to a character using the passage (e.g. "his wife").
- A quotation in the same paragraph as a tagged one is usually the same speaker, unless the text says otherwise.
- In untagged back-and-forth conversation, speakers usually alternate between the two people talking.
- What a quotation says (who it addresses, what it refers to) is evidence: people do not usually address themselves by name.
- Characters may be named by any alias listed.
Text marked [context] is earlier or later prose for reference only. Label only marked quotations."""


def render_passage(context: list[list[Span]], window: list[list[Span]], following=()) -> tuple[str, dict[int, str]]:
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
    lines.extend("[context] " + "".join(s.text for s in para) for para in following)
    return "\n\n".join(lines), markers


def fit_context(before, after, room: int):
    """Take the nearest paragraphs on each side without displacing the target."""
    leading, trailing = [], []
    open_side = [True, True]
    for prior, later in zip_longest(reversed(before), after):
        for side, (paragraph, selected) in enumerate(((prior, leading), (later, trailing))):
            if open_side[side] and paragraph is not None:
                size = 14 + sum(len(span.text) for span in paragraph)
                if size <= room:
                    selected.append(paragraph)
                    room -= size
                else:
                    open_side[side] = False
        if room <= 0 or not any(open_side):
            break
    return leading[::-1], trailing


NARRATOR_SYSTEM = """This novel is narrated in the first person. From the passage and the character list, decide
who the narrator ("I") is. Give a one-sentence reason, then the character id, or "unnamed" if the narrator is
never named or is not in the list."""


def role_key(role: str) -> str:
    """Normalised role for grouping: "The Charwoman" / "a charwoman" / "charwoman," -> "charwoman"."""
    words = re.findall(r"[a-z0-9'’-]+", role.casefold())
    while words and words[0] in ("the", "a", "an"):
        words = words[1:]
    return " ".join(words)


_FRONT = re.compile(r"(?i)^(front matter|preface|foreword|introduction|note|dedication|contents|author|editor|"
                    r"translator|advertisement|prologue to the|biographical|transcriber)")


def first_person_rate(spans: list[Span]) -> float:
    text = " ".join(s.text for s in spans if s.kind is SpanKind.narration)
    return 1000 * len(_FIRST_PERSON.findall(text)) / max(1, len(text.split()))


# Relationship/role nouns that dialogue tags use for characters ("said the nephew", "his clerk").
ROLE_NOUNS = {"nephew", "niece", "uncle", "aunt", "cousin", "brother", "sister", "mother", "father", "son", "daughter",
              "wife", "husband", "widow", "clerk", "servant", "maid", "housekeeper", "butler", "landlord", "landlady",
              "master", "mistress", "doctor", "captain", "colonel", "inspector", "constable", "governess", "nurse",
              "coachman", "footman", "porter", "lawyer", "partner", "girl", "boy", "child", "grandmother", "grandfather"}


def roster_for(chars: CharacterList, chapter_id: str, top: int, full_max: int = 0, text: str = "") -> list[Character]:
    """The chapter's characters, the book's `top` most-mentioned, and — from the chapter text — anyone named
    there (by name or alias) and speaking characters whose described role appears there ("Scrooge's nephew"
    for a stave that only says "the nephew"). Without per-chapter data, or for small casts when full_max
    allows, every speaking character."""
    ranked = sorted(chars.characters, key=lambda c: -c.mentions)
    keep = {c.id for c in chars.characters if chapter_id in c.chapters}
    # The book's main characters, unless chapter data says they're elsewhere and the text doesn't name them
    # (top-N pulled every ghost into every stave of A Christmas Carol).
    keep |= {c.id for c in ranked[:top] if not text or not c.chapters}
    if text:
        low = text.casefold()
        words = set(re.findall(r"[a-z]+", low))
        ambiguous = {a.casefold() for a in chars.ambiguous_aliases}

        def distinctive(n: str) -> bool:
            # Names claimed by several characters ("the Spirit" in A Christmas Carol) say nothing about who is
            # present. A title only one character has ("the captain", "the Count") does.
            return len(n) >= 3 and n.casefold() not in ambiguous

        for c in chars.characters:
            if c.origin != "profile":
                continue
            if any(distinctive(n) and re.search(rf"(?<!\w){re.escape(n.casefold())}(?!\w)", low)
                   for n in (c.canonical_name, *c.aliases)):
                keep.add(c.id)
            elif c.speaks and c.description and (set(re.findall(r"[a-z]+", c.description.casefold())) & ROLE_NOUNS & words):
                keep.add(c.id)
    speaking = {c.id for c in chars.characters if c.speaks and c.origin == "profile"}
    if not any(c.chapters for c in chars.characters) or len(speaking) <= full_max:
        keep |= speaking
    return [c for c in ranked if c.id in keep]


def roster_text(roster: list[Character], narrator: str | None = None) -> str:
    lines = []
    if narrator == NARRATOR_ID:
        lines.append(f"{NARRATOR_ID}: the unnamed first-person narrator (\"I\")")
    for c in roster:
        al = f"; also {', '.join(c.aliases[:6])}" if c.aliases else ""
        desc = f" — {c.description[:100]}" if c.description else ""
        quiet = " [not seen speaking]" if not c.speaks and c.origin == "profile" else ""
        lines.append(f"{c.id}: {c.canonical_name} ({c.gender.value}{al}){desc}{quiet}")
    return "\n".join(lines)


def label_schema(n: int, ids: list[str]) -> type[BaseModel]:
    speaker_t = Literal[tuple(ids + [OTHER, UNKNOWN])]  # type: ignore[valid-type]
    marker_t = Literal[tuple(range(1, n + 1))]  # type: ignore[valid-type]
    Label = create_model("Label", id=(marker_t, ...), cue=(str, Field(max_length=80)), speaker=(speaker_t, ...),
                         other_role=(str, Field(max_length=40)),
                         other_gender=(Literal["male", "female", "unknown"], ...),
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
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'  # the code itself; the LLM's terms are reported separately
    USES_LLM = True
    Settings = SpeakersSettings
    settings: SpeakersSettings

    def fingerprint(self) -> str:
        return f"{super().fingerprint()}:{self.cfg.llm.model}:{self.cfg.llm.context_tokens}:{self.cfg.llm.min_max_tokens}"

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
        errors_file = wd.root / "attribution_errors.json"
        if errors_file.exists():
            errors_file.unlink()
        chars = wd.read_characters()
        if not chars:
            raise RuntimeError("speakers needs characters.json — run the parse stage with the 'characters' method")
        if not chars.characters:
            words = sum(c.word_count for c in wd.read_chapters().chapters)
            raise RuntimeError(f"character profiling found no characters in {words:,} words of text — check "
                               f"{wd.text_path} (a short or wrong page?), or use attribute=narrator for one voice")
        llm = LLM(self.cfg.llm, cache_dir=wd.root / "llm_cache")
        # Rebuild unnamed speakers only for the chapters being attributed.
        chars.characters = [c for c in chars.characters if c.origin != "attribution" or c.locked
                            or (ctx.chapters is not None and c.first_chapter not in ctx.chapters)]
        chars.narrator = self._find_narrator(llm, wd, chars, ctx)
        wd.write_characters(chars)

        book: dict[str, list[Span]] = {}
        jobs = []
        for ch in wd.read_chapters().chapters:
            if ctx.chapters is not None and ch.id not in ctx.chapters:
                if not wd.spans_path(ch.id).exists():  # keep other chapters' existing labels
                    wd.write_spans(ch.id, split_chapter(ch.id, wd.read_chapter_text(ch.file)))
                continue
            text = wd.read_chapter_text(ch.file)
            spans = split_chapter(ch.id, text)
            book[ch.id] = spans
            paras: list[list[Span]] = []
            for s in spans:
                if not paras or paras[-1][0].para != s.para:
                    paras.append([])
                paras[-1].append(s)
            roster = roster_for(chars, ch.id, self.settings.roster_top, self.settings.full_roster_max, text=text)
            if chars.narrator and chars.narrator != NARRATOR_ID and chars.narrator not in {c.id for c in roster}:
                roster.insert(0, next(c for c in chars.characters if c.id == chars.narrator))
            for a, b in windows(paras, self.settings.max_quotes, self.settings.max_window_words):
                limit = self.settings.context_paragraphs
                start = 0 if limit is None else max(0, a - limit)
                end = len(paras) if limit is None else b + limit
                jobs.append((ch.id, paras[start:a], paras[a:b], roster, chars.narrator, paras[b:end]))

        labels: dict[str, tuple] = {}
        failed = 0
        failed_windows: list[dict] = []
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.llm.concurrency)) as ex:
            futs = {ex.submit(self._label_with_retry, llm, *j): j for j in jobs}
            for done, f in enumerate(as_completed(futs), 1):
                j = futs[f]
                try:
                    labels.update(f.result())
                except LLMError as e:
                    failed += 1
                    ch_id = j[0]
                    failed_windows.append({
                        "chapter": ch_id,
                        "quotes": sum(s.kind is SpanKind.dialogue for p in j[2] for s in p),
                        "error": str(e),
                    })
                    ctx.log(f"  window failed: {e}")
                if done % 20 == 0 or done == len(jobs):
                    ctx.log(f"  labelled {done}/{len(jobs)} windows")

        if failed_windows:
            wd.root.mkdir(parents=True, exist_ok=True)
            errors_file.write_text(json.dumps(failed_windows, indent=2) + "\n")

        minor = self._minor_characters(chars, labels, book)
        counts: dict[str, int] = {}
        total = unknown = locked = 0
        for cid, spans in book.items():
            for s in spans:
                if s.kind is SpanKind.dialogue:
                    total += 1
                    who, emo, role, _ = labels.get(s.id, (UNKNOWN, None, "", "unknown"))
                    if who == OTHER:
                        who = minor.get((cid, role_key(role)), UNKNOWN)
                    elif who == NARRATOR_ID and chars.narrator and chars.narrator != NARRATOR_ID:
                        who = chars.narrator  # first person: the narrator *is* that character (same voice)
                    s.speaker = None if who == UNKNOWN else who
                    s.emotion = emo
            locked += keep_locked(wd, cid, spans)  # hand-reviewed labels win
            for s in spans:
                if s.kind is SpanKind.dialogue:
                    unknown += s.speaker is None
                    if s.speaker:
                        counts[s.speaker] = counts.get(s.speaker, 0) + 1
            wd.write_spans(cid, spans)
        if locked:
            ctx.log(f"  kept {locked} hand-reviewed spans")

        if ctx.chapters is None:  # line counts drive casting priority; only meaningful for a full run
            for c in chars.characters:
                c.line_count = counts.get(c.id, 0)
        wd.write_characters(chars)
        n_minor = len(set(minor.values()))
        ctx.log(f"  {total:,} quotations: {total - unknown:,} attributed, {unknown:,} unknown"
                + (f"; {n_minor} unnamed speakers added as minor characters" if n_minor else "")
                + (f"; WARNING {failed} windows failed" if failed else ""))
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
        ctx.log("  most lines: " + ", ".join(f"{k} {v}" for k, v in top))
        return counts

    @staticmethod
    def _minor_characters(chars: CharacterList, labels: dict[str, tuple], book: dict[str, list[Span]]) -> dict:
        """Turn "other" labels into minor characters, one per (chapter, role). A role that names an existing
        character's alias maps to that character instead. Returns {(chapter, role key): character id}."""
        by_alias = {}
        for c in chars.characters:
            for n in (c.canonical_name, *c.aliases):
                by_alias.setdefault(role_key(n), c.id)
        roles: dict[tuple[str, str], dict] = {}
        for cid, spans in book.items():
            for s in spans:
                who, _, role, gender = labels.get(s.id, (UNKNOWN, None, "", "unknown"))
                k = role_key(role)
                if who == OTHER and k:
                    r = roles.setdefault((cid, k), {"label": role.strip(), "genders": Counter()})
                    r["genders"][gender] += 1
        out: dict[tuple[str, str], str] = {}
        existing = {c.id for c in chars.characters}
        for (cid, k), r in roles.items():
            if k in by_alias:
                out[(cid, k)] = by_alias[k]
                continue
            mid = f"minor-{cid}-{slugify(k)}"
            out[(cid, k)] = mid
            if mid in existing:  # a hand-edited (locked) one from a previous run
                continue
            known = {g: n for g, n in r["genders"].items() if g != "unknown"}
            gender = max(known, key=known.get) if known else "unknown"
            label = r["label"][0].upper() + r["label"][1:]
            chars.characters.append(Character(id=mid, canonical_name=label, gender=Gender(gender), chapters=[cid],
                                              first_chapter=cid, speaks=True, origin="attribution",
                                              description="unnamed speaker identified during attribution"))
            existing.add(mid)
        return out

    def _find_narrator(self, llm: LLM, wd, chars: CharacterList, ctx: Context) -> str | None:
        """None for third-person books; else the narrating character's id (or NARRATOR_ID if unnamed)."""
        sample: list[Span] = []
        for ch in wd.read_chapters().chapters:
            sample += split_chapter(ch.id, wd.read_chapter_text(ch.file))
        rate = first_person_rate(sample)
        if rate < self.settings.first_person_rate:
            ctx.log(f"  third-person narration ({rate:.1f} first-person words per 1000)")
            return None
        # Sample throughout the story, excluding prefaces and notes.
        chapters = [ch for ch in wd.read_chapters().chapters if not _FRONT.match(ch.title)] or wd.read_chapters().chapters
        count = min(len(chapters), self.settings.narrator_chapters)
        picks = [chapters[round(i * (len(chapters) - 1) / max(1, count - 1))] for i in range(count)]
        roster = sorted(chars.characters, key=lambda c: -c.mentions)
        while True:
            ids = [c.id for c in roster] + [UNNAMED]
            schema = create_model("Narrator", reason=(str, Field(max_length=300)), narrator=(Literal[tuple(ids)], ...))
            roster_prompt = f"CHARACTERS:\n{roster_text(roster)}\n\nPASSAGE:\n"
            room = prompt_char_budget(self.cfg.llm, NARRATOR_SYSTEM, schema, 1024, self.settings.context_tokens) - len(roster_prompt)
            if room >= 256 or not roster:
                break
            roster = roster[:len(roster) // 2]
        if room < 256:
            raise LLMError("narrator identification has insufficient context for the prompt")
        excerpts = []
        passages = [(f"[chapter {c.id}]\n", wd.read_chapter_text(c.file)) for c in picks[:room // 64]]
        room -= sum(len(header) + 2 for header, _ in passages)
        for n, (header, text) in enumerate(sorted(passages, key=lambda p: len(p[1]))):
            share = room // (len(passages) - n)
            if len(text) > share:
                edge = (share - 7) // 2
                text = text[:edge] + "\n[…]\n" + text[-edge:] if edge > 0 else text[:share]
            excerpts.append((header, text))
            room -= len(text)
        excerpt = "\n\n".join(header + text for header, text in sorted(excerpts))
        r = llm.chat_json(NARRATOR_SYSTEM, roster_prompt + excerpt, schema,
                          think=False, max_tokens=1024)
        who = NARRATOR_ID if r.narrator == UNNAMED else r.narrator
        ctx.log(f"  first-person narration ({rate:.1f}/1000): narrator = {who} — {r.reason}")
        return who

    def _label(self, llm: LLM, chapter: str, context, window, roster: list[Character],
               narrator: str | None = None, following=()) -> dict[str, tuple[str, Emotion | None]]:
        text, markers = render_passage([], window)
        ids = [c.id for c in roster] + [NARRATOR_ID]
        schema = label_schema(len(markers), ids)
        note = ""
        if narrator:
            who = "an unnamed narrator (id narrator)" if narrator == NARRATOR_ID else f"{narrator}"
            note = (f"NOTE: this book is narrated in the first person by {who}. \"I\"/\"me\" in the narration is that "
                    f"character, so quotations the narrator speaks (\"said I\", \"I answered\") belong to {narrator}.\n\n")
        prefix = f"{note}CHARACTERS:\n{roster_text(roster, narrator)}\n\nPASSAGE:\n"
        s = self.settings
        output = s.max_tokens_thinking if s.think else s.max_tokens
        room = prompt_char_budget(self.cfg.llm, SYSTEM, schema, output, s.context_tokens) - len(prefix) - len(text)
        if room < 0:
            raise LLMError(f"chapter {chapter} target quotations exceed the prompt budget")
        before, after = fit_context(context, following, room)
        passage, _ = render_passage(before, window, after)
        r = llm.chat_json(SYSTEM, prefix + passage, schema, think=s.think, max_tokens=output)
        returned = [q.id for q in r.quotes]
        if len(returned) != len(markers) or set(returned) != markers.keys():
            raise LLMError(f"chapter {chapter} returned missing, duplicate or unexpected quote IDs")
        out = {}
        for q in r.quotes:
            sid = markers.get(q.id)
            if sid and sid not in out:
                out[sid] = (q.speaker, self.emotion(q.emotion, q.intensity), q.other_role, q.other_gender)
        return out

    def _label_with_retry(self, llm: LLM, chapter, before, window, roster, narrator=None, following=(),
                          *, retries: int = 2) -> dict[str, tuple]:
        for attempt in range(retries + 1):
            try:
                return self._label(llm, chapter, before, window, roster, narrator, following)
            except LLMError as e:
                detail = str(e)
                if detail.startswith(("hit max_tokens=", "invalid Labels JSON")) or detail.startswith(f"chapter {chapter} "):
                    if len(window) > 1:
                        mid = len(window) // 2
                        left, right = window[:mid], window[mid:]
                    elif len(window[0]) > 1:
                        mid = len(window[0]) // 2
                        left, right = [window[0][:mid]], [window[0][mid:]]
                    else:
                        raise
                    out = {}
                    for prefix, target, suffix in ((before, left, [*right, *following]),
                                                   ([*before, *left], right, following)):
                        if any(span.kind == SpanKind.dialogue for para in target for span in para):
                            out.update(self._label_with_retry(llm, chapter, prefix, target, roster, narrator, suffix,
                                                              retries=retries))
                    return out
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise


