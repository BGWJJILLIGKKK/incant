"""Remove text that isn't the story: author's notes, introductions, commentary, sign-offs and site furniture
at the start and end of each chapter ("This is a story about…", "~A friend commissioned this story",
"Thanks for reading!", "Recommended Next Steps", "Click here to send the author a tip").

Only runs of paragraphs touching a chapter's start or end are removed, never text in the middle, and never
more than `max_share` of a chapter. A scene break (* * *) right after an opening note (or right before a
closing one) goes with it. Everything removed is listed in removed.txt in the book's work directory.

Modes:
  rules  phrase and style cues (reader address, talk about "this story", calls to comment/fave/vote, links,
         "~" note lines, emoticons); dialogue counts as story. No LLM.
  llm    the LLM labels the paragraphs at each chapter's edges (cached); a label counts only if the paragraph
         also shows a note cue, is a title line or is short (a 9B model once labelled 1,300 words of a story's
         ending as notes). The same edge-only and share limits apply.
  auto   rules for web pages (ingest=url), off for books (epub/pdf/txt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ...config import LLMConfig
from ...llm import LLMError, prompt_char_budget
from ...normalize import SCENE_BREAK

Mode = Literal["auto", "off", "rules", "llm"]

# A paragraph matching one of these is an author's note / site text on its own.
_STRONG = [re.compile(p, re.I) for p in (
    r"^\s*~",                                                     # "~A friend commissioned this story"
    r"^\s*\(?\s*(?:a/?n|author['’]?s? notes?|notes?|edit(?:ed)?|update[d]?|disclaimer|warning|content warnings?|"
    r"tags?|summary|synopsis|p\.?s\.?)\s*\)?\s*[:\-–—]",
    r"\bthanks?(?: you)?(?: so much)? for (?:reading|all the|the support|your support)",
    r"\b(?:leave|drop|post|write) (?:a |me a |some )?(?:comment|review|note|fave|like)",
    # a call to action, as an imperative: "Please comment below", "Feel free to fave it", "VOTE if you read it"
    r"(?:^|[.!?;]\s+|\bplease\s+|\bfeel (?:free|encouraged) to\s+|\bforget to\s+)(?:and\s+)?"
    r"(?:comment|fave|favou?rite|vote|rate|review|share|follow|subscribe|watch)\b[^.]{0,40}"
    r"\b(?:below|story|chapter|it|this|me|gallery|author)\b",
    r"(?-i:\b(?:VOTE|COMMENT|FAVE|FAV|PAW|SHARE|REVIEW)\b)",
    r"\bin the comments?\b|\bcomments? (?:below|are welcome|appreciated)",
    r"\bif you (?:liked|loved|enjoyed|like|love|enjoy)\b[^.]{0,40}\b(?:it|this|story|chapter)\b",
    r"\b(?:patreon|ko-?fi|subscribestar|commission(?:ed|s)?|tip jar|send the author|donat(?:e|ion))",
    r"https?://|www\.[a-z]|\b[a-z0-9-]+\.(?:com|net|org)/",
    r"\b(?:recommended next steps|click here|next chapter|previous chapter|(?:to be )?continued in (?:part|chapter)|read (?:it )?here|"
    r"to be continued|more to come|stay tuned|part \w+ (?:is|will be) (?:up|out|coming))\b",
    r"\b(?:sequel|prequel|follow-?up) (?:to|of)\b",
    r"\b(?:this|my) (?:story|fic|fanfic|fanfiction|rp|role-?play) (?:is|was|contains|features)\b",
    r"\bthis is (?:a|my) (?:story|fic|tale|smut|rp)\b",
    r"\b(?:copyright|all rights reserved)\b|©|\bcharacters? (?:belong|are owned|©)",
    r"\b(?:spell-?check|proof-?read|beta[- ]?read|self[- ]promot|first (?:story|fic|time writing))",
    r"\bRP between\b|\bwritten (?:by|with|for) (?:myself|me)\b",
    r"\bctrl\s*\+?\s*f\b|\bskip (?:ahead|to the)\b",
)]
# Softer cues: two of them (or one in a very short paragraph) make a note.
_SOFT = [re.compile(p, re.I) for p in (
    r"\b(?:you|your|you'll|you're)\b[^.]{0,60}\b(?:read|reader|enjoy|story|like|love)",
    r"\b(?:readers|reading this)\b",
    r"\bI(?:'m| am| have|'ve| had)? (?:wrote|written|writing|write|posted|post|uploaded)\b",
    r"\b(?:story|stories|fic|smut|chapters?|plot|series|storyboard\w*|sequel)\b",
    r"(?:^|\s)(?::[PpD)(]|;\)|\^_?\^|XD|xD|<3|D:|:-?\))(?:\s|$|[.!])",
    r"\b(?:lol|lulz|lmao|dunno|anywho|btw|tbh|imo|haha)\b",
    r"\b(?:enjoy|hope you)\b",
    r"\b(?:author|artist|writer)\b",
)]
_DIALOGUE = re.compile(r"[“\"][^”\"]{12,}[”\"]")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def is_title_line(p: str, title: str) -> bool:
    """The book's title (or its first part) repeated as a paragraph, or a bare heading ("Chapter 1")."""
    if len(p.split()) > 12 or re.search(r"[.!?]\s*$", p.strip()) and not p.strip().endswith("..."):
        return False
    n, t = _norm(p), _norm(title)
    if re.fullmatch(r"\d{1,4}", n):
        return True  # a page number
    n = re.sub(r" by [a-z0-9 ]{1,40}$", "", n)  # "Title by Author"
    return bool(n) and (n == t or (t.startswith(n) and len(n) >= 4)
                        or (len(t) >= 4 and n.startswith(t + " ") and len(n.split()) <= 10)  # "Series: Part Title"
                        or bool(re.fullmatch(r"(?:chapter|part) \w+", n)))


def score(p: str) -> tuple[bool, str]:
    """(is a note, why)."""
    if p == SCENE_BREAK:
        return False, ""
    strong = next((rx.pattern for rx in _STRONG if rx.search(p)), None)
    if strong and not (_DIALOGUE.search(p) and not p.lstrip().startswith("~")):
        return True, "note phrase"
    if _DIALOGUE.search(p):
        return False, ""
    soft = sum(bool(rx.search(p)) for rx in _SOFT)
    words = len(p.split())
    if soft >= 2 or (soft >= 1 and words <= 4):
        return True, f"{soft} note cues"
    return False, ""


def any_cue(p: str, title: str) -> bool:
    """Something note-like about the paragraph: a cue phrase, a title line, or very short."""
    return (any(rx.search(p) for rx in _STRONG) or any(rx.search(p) for rx in _SOFT)
            or is_title_line(p, title) or len(p.split()) <= 12)


@dataclass
class Removed:
    chapter: int  # index into the chapter list
    where: Literal["start", "end"]
    text: str
    why: str


def _edge(paras: list[str], is_meta: list[bool], max_scan: int) -> int:
    """How many paragraphs from the start of `paras` are a note block: the longest opening run that ends on a
    note, is at least two-thirds notes and has no dialogue among the rest; or — when a scene break comes early
    and most of what precedes it is notes — everything up to and including that break."""
    n = 0
    meta = 0
    for k in range(min(max_scan, len(paras))):
        p = paras[k]
        if p == SCENE_BREAK:
            if k and meta * 3 >= 2 * k and is_meta[k - 1]:
                n = k + 1  # the break closes the note block
            break
        if not is_meta[k] and _DIALOGUE.search(p):
            break  # story has started
        meta += is_meta[k]
        if is_meta[k] and meta * 3 >= 2 * (k + 1):
            n = k + 1
    window = paras[:max_scan]
    if SCENE_BREAK in window:
        k = window.index(SCENE_BREAK)
        if k and sum(is_meta[:k]) >= max(1, (k + 1) // 2):
            n = max(n, k + 1)
    return n


class MetatextSettings(BaseModel):
    """Shared by parse methods: [parse.<method>] metatext = "auto" | "off" | "rules" | "llm"."""

    metatext: Mode = "auto"
    metatext_max_share: float = Field(0.3, ge=0.0, le=1.0)  # never remove more than this share of a chapter
    metatext_scan: int = 12  # paragraphs examined at each end of a chapter

    def metatext_fingerprint(self) -> dict:
        """Settings that change output, minus the defaults (so adding this option didn't re-parse every book)."""
        d = self.model_dump(include={"metatext", "metatext_max_share", "metatext_scan"})
        if self.metatext == "llm":
            d["prompt"] = "full-edge-paragraphs"
        return {} if d == MetatextSettings().model_dump(include=set(d)) else d


class _Labels(BaseModel):
    not_story: list[int] = Field(description="numbers of the paragraphs that are not part of the story")


LLM_SYSTEM = """You clean up stories copied from the web for an audiobook. You see the first and last paragraphs \
of a chapter, numbered. Some may not be part of the story: an author's note, introduction or foreword in the \
author's own voice, commentary about the writing, content warnings or tags, thanks, requests to comment, fave, \
vote or tip, links, update notes, site navigation, or a repeated title line. List the numbers of those paragraphs \
only. Story text, including narration, dialogue, epigraphs that belong to the story, and chapter openings, is \
NOT listed. When unsure, do not list it."""


def llm_labels(llm, title: str, paras: list[str], idx: list[int]) -> set[int]:
    prefix = f"TITLE: {title}\n\n"
    room = prompt_char_budget(getattr(llm, "cfg", LLMConfig()), LLM_SYSTEM, _Labels, 512) - len(prefix)
    if room < 128:
        raise LLMError("author-note detection has insufficient context for the prompt")
    labels = set()
    lines: list[str] = []
    sent: set[int] = set()
    size = 0
    previous = None

    def flush():
        if sent:
            result = llm.chat_json(LLM_SYSTEM, prefix + "\n\n".join(lines), _Labels, max_tokens=512)
            labels.update(set(result.not_story) & sent)

    for i in idx:
        label = f"[{i}] "
        text = paras[i]
        if len(label) + len(text) > room:
            # A partly visible paragraph is unsafe to classify as removable.
            continue
        gap = "[… intervening paragraphs omitted …]\n\n" if previous is not None and i != previous + 1 else ""
        line = label + text
        if size + len(gap) + len(line) + 2 > room:
            flush()
            lines, sent, size, gap = [], set(), 0, ""
        lines.append(gap + line)
        sent.add(i)
        size += len(gap) + len(line) + 2
        previous = i
    flush()
    return labels


def strip_metatext(chapters: list[list[str]], title: str, mode: str, settings: MetatextSettings,
                   llm=None) -> tuple[list[list[str]], list[Removed], list[str]]:
    """Returns (chapters with notes removed, what was removed, notes for the log)."""
    removed: list[Removed] = []
    log: list[str] = []
    out: list[list[str]] = []
    for ci, paras in enumerate(chapters):
        if not paras:
            out.append(paras)
            continue
        scan = settings.metatext_scan
        why: dict[int, str] = {}
        if mode == "llm" and llm is not None:
            idx = sorted(set(range(min(scan, len(paras)))) | set(range(max(0, len(paras) - scan), len(paras))))
            idx = [i for i in idx if paras[i] != SCENE_BREAK]
            for i in llm_labels(llm, title, paras, idx):
                if any_cue(paras[i], title):  # long plain narration is never removed on the LLM's word alone
                    why[i] = "LLM"
        else:
            for i, p in enumerate(paras):
                if i < scan or i >= len(paras) - scan:
                    meta, w = score(p)
                    if meta:
                        why[i] = w
        for i in [*range(min(scan, len(paras))), *range(max(0, len(paras) - scan), len(paras))]:
            if i not in why and is_title_line(paras[i], title):  # repeated title, byline, bare heading, page number
                why[i] = "title line"
        is_meta = [i in why for i in range(len(paras))]
        head = _edge(paras, is_meta, scan)
        while head < len(paras) and head and is_title_line(paras[head], title):  # the story's own title after a note
            why[head] = "title line"
            head += 1
        tail = _edge(paras[::-1], is_meta[::-1], scan)
        while tail and len(paras) - tail - 1 > head and is_title_line(paras[len(paras) - tail - 1], title):
            why[len(paras) - tail - 1] = "title line"  # a byline or page number left above closing notes
            tail += 1
        if head + tail >= len(paras):
            log.append(f"chapter {ci + 1}: every paragraph looked like a note; kept as is")
            out.append(paras)
            continue
        cut = [*range(head), *range(len(paras) - tail, len(paras))]
        words = sum(len(paras[i].split()) for i in cut if paras[i] != SCENE_BREAK)
        total = sum(len(p.split()) for p in paras if p != SCENE_BREAK) or 1
        if words / total > settings.metatext_max_share:
            log.append(f"chapter {ci + 1}: notes would be {words / total:.0%} of the text (max "
                       f"{settings.metatext_max_share:.0%}); kept as is")
            out.append(paras)
            continue
        for i in range(head):
            removed.append(Removed(ci, "start", paras[i], why.get(i, "scene break")))
        for i in range(len(paras) - tail, len(paras)):
            removed.append(Removed(ci, "end", paras[i], why.get(i, "scene break")))
        if head or tail:
            log.append(f"chapter {ci + 1}: removed {head} opening and {tail} closing paragraph(s) of notes ({words} words)")
        out.append(paras[head:len(paras) - tail])
    return out, removed, log


def report(removed: list[Removed], mode: str) -> str:
    lines = [f"# Text removed as author's notes / site text (metatext = {mode})",
             "# Wrong? Set metatext = \"off\" under [parse.<method>] and re-run with --force parse.", ""]
    for r in removed:
        lines.append(f"[chapter {r.chapter + 1}, {r.where}] ({r.why})")
        lines.append(r.text)
        lines.append("")
    return "\n".join(lines)
