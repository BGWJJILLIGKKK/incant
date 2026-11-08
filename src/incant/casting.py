"""Automatic casting: speaking characters -> voices from the library's gender/age tags.

Roles still equal to their previous automatic pick (cast.auto) are recomputed each run, so newly tagged
voices take effect; roles a person changed or added are kept and count as fixed usage.
Most lines first; for each character the best voice minimises, in order:
  1. chapters shared with characters already using that voice (two people in one scene shouldn't sound alike)
  2. sharing with a main character (the top `main_unique` speakers keep a voice to themselves)
  3. reuse at all (spread over the library before doubling up)
  4. age mismatch (character age bracket -> preferred voice age tags), then tone (young -> high/neutral)
  5. lines already carried by that voice
Gender is a hard filter when both sides are tagged; untagged voices are not used for casting.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .schemas import NARRATOR_ID, Cast, Character, Span, SpanKind, Voice

# character age bracket -> voice `age` tags in order of preference (the library's own vocabulary)
AGE_PREFS: dict[str, list[str]] = {
    "child": ["child", "teen", "neutral", "adult"],
    "teen": ["teen", "neutral", "adult"],
    "young_adult": ["adult", "neutral", "teen"],
    "adult": ["adult", "neutral", "older"],
    "elderly": ["older", "adult", "neutral"],
    "unknown": ["adult", "neutral", "teen", "older"],
}


# character age bracket -> voice `tone` tags in order of preference (tie-breaker after age)
TONE_PREFS: dict[str, list[str]] = {
    "child": ["high", "neutral", "deep"],
    "teen": ["high", "neutral", "deep"],
    "young_adult": ["neutral", "high", "deep"],
    "adult": ["neutral", "deep", "high"],
    "elderly": ["deep", "neutral", "high"],
    "unknown": ["neutral", "deep", "high"],
}


@dataclass
class _Use:
    chars: list[str] = field(default_factory=list)
    chapters: set[str] = field(default_factory=set)
    lines: int = 0
    main: bool = False


def speaking_stats(spans_by_chapter: dict[str, list[Span]]) -> tuple[dict[str, int], dict[str, set[str]]]:
    lines: dict[str, int] = defaultdict(int)
    chapters: dict[str, set[str]] = defaultdict(set)
    for cid, spans in spans_by_chapter.items():
        for s in spans:
            if s.kind is SpanKind.dialogue and s.speaker and s.speaker != NARRATOR_ID:
                lines[s.speaker] += 1
                chapters[s.speaker].add(cid)
    return dict(lines), dict(chapters)


def auto_cast(cast: Cast, characters: list[Character], spans_by_chapter: dict[str, list[Span]],
              voices: dict[str, Voice], main_unique: int = 8) -> list[tuple[str, str, str]]:
    """Recast automatic roles and fill uncast speaking characters. Returns [(character id, voice id, note)]."""
    for cid, vid in list(cast.auto.items()):
        if cast.roles.get(cid) == vid:
            del cast.roles[cid]  # still the automatic pick: recompute it
    cast.auto = {}
    lines, chapters = speaking_stats(spans_by_chapter)
    by_id = {c.id: c for c in characters}
    pool = {vid: v for vid, v in voices.items() if (v.gender or "").lower() in ("male", "female")}
    narrator_voice = cast.roles.get(NARRATOR_ID) or cast.default_voice

    speakers = sorted(lines, key=lambda c: (-lines[c], c))
    main = set(speakers[:main_unique])
    use: dict[str, _Use] = defaultdict(_Use)
    for cid, vid in cast.roles.items():  # existing (manual or earlier) roles count as usage
        if cid == NARRATOR_ID:
            continue
        u = use[vid]
        u.chars.append(cid)
        u.chapters |= chapters.get(cid, set())
        u.lines += lines.get(cid, 0)
        u.main |= cid in main

    new = []
    for cid in speakers:
        if cid in cast.roles:
            continue
        c = by_id.get(cid)
        gender = c.gender.value if c else "unknown"
        age = c.age.value if c else "unknown"
        prefs = AGE_PREFS.get(age, AGE_PREFS["unknown"])
        tones = TONE_PREFS.get(age, TONE_PREFS["unknown"])
        cands = [v for v in pool.values() if gender not in ("male", "female") or v.gender.lower() == gender]
        if not cands:
            continue  # nothing suitable: falls back to the default voice at render time
        non_narrator = [v for v in cands if v.id != narrator_voice]
        cands = non_narrator or cands
        mine = chapters.get(cid, set())

        def key(v: Voice):
            u = use.get(v.id, _Use())
            age_rank = prefs.index(v.age.lower()) if v.age and v.age.lower() in prefs else len(prefs)
            tone_rank = tones.index(v.tone.lower()) if v.tone and v.tone.lower() in tones else len(tones)
            return (len(mine & u.chapters), u.main, bool(u.chars), age_rank, tone_rank, u.lines, v.id)

        v = min(cands, key=key)
        u = use[v.id]
        overlap = len(mine & u.chapters)
        note = "" if not u.chars else f"shared with {', '.join(u.chars[:3])}" + (f" ({overlap} common chapters)" if overlap else "")
        cast.roles[cid] = v.id
        cast.auto[cid] = v.id
        u.chars.append(cid)
        u.chapters |= mine
        u.lines += lines[cid]
        u.main |= cid in main
        new.append((cid, v.id, note))
    return new
