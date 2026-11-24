"""Deterministic chapters + LLM character profiles (names, aliases, gender, age, description).

1. extract — per text window (chapters split at ~window_words), in parallel, no thinking. Output is
             bounded by the JSON grammar (list/string lengths) so a small model can't run away.
2. verify  — names and aliases must literally occur in their window; pronouns, descriptions
             ("his wife"), possessives ("Bingley's sister") and groups are dropped.
3. pool    — entries with the same name are pooled across the book.
4. link    — code proposes candidate matches (shared name core or alias, compatible gender); the LLM
             answers one small question per entry: "same person as which candidate, if any?".
             A quick answer stands only if the names corroborate it; otherwise the question is
             re-asked with thinking (the quick pass over-merges, the thinking pass under-merges).
             Each entry attaches to at most one larger entry, so a shared title like "Miss Bennet"
             can't chain two sisters together.
5. profile — gender/age by vote; aliases claimed by several characters stay only with the
             characters that use them often; remaining collisions are listed as ambiguous.

Characters marked "locked": true in characters.json are kept as-is on re-runs.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from ....llm import LLM, LLMError, estimate_tokens, prompt_char_budget
from ....normalize import slugify
from ....schemas import AgeBracket, Character, CharacterList, Gender, SpanKind
from ....workdir import WorkDir
from ...base import Context
from ...attribute.spans import split_paragraph
from ..base import ParseMethod
from ..chapters import build_chapters
from ..metatext import MetatextSettings

__all__ = ["CharactersParse"]


class CharactersSettings(MetatextSettings):
    # Chapters longer than this are split for extraction. A 9B model gets sloppy on big windows (aliases
    # attached to the wrong person, characters missed): 2000 words is close to a typical novel chapter.
    window_words: int = 2000
    extraction_mode: Literal["chapter", "neighbors", "batch"] = "neighbors"
    context_tokens: int = Field(default=262144, gt=0)  # prompt and completion per extraction request
    context_chapters: int = Field(default=1, ge=0)  # on either side of each target
    batch_chapters: int = Field(default=4, ge=1)
    batch_max_tokens: int = Field(default=16384, gt=0)
    think: bool = False  # for extraction: ~10x slower per window for little gain
    max_tokens: int = 4096
    # Linking: a fast answer is accepted when the names corroborate it ("George Wickham" ⊇ "Wickham");
    # every other question is re-asked with thinking, which is slower but far more precise.
    verify_links_with_thinking: bool = True
    # Thinking that runs past this is looping (a 9B model sometimes never converges); the link is
    # then skipped, which is the conservative outcome. Lower = less time wasted on those.
    max_tokens_thinking: int = 6144
    max_characters_per_window: int = 40
    link_candidates: int = Field(default=12, ge=1)


# ------------------------------------------------------------------ LLM schemas + prompts


class _Mention(BaseModel):
    name: str = Field(max_length=60, description="the person's name or title as written in the passage")
    aliases: list[str] = Field(max_length=8, description="other names, nicknames or titles used for this same person")
    gender: Literal["male", "female", "nonbinary", "unknown"]
    gender_evidence: str = Field(max_length=60, description="pronoun or phrase from the passage; empty if unknown")
    age: Literal["child", "teen", "young_adult", "adult", "elderly", "unknown"]
    description: str = Field(max_length=160, description="who they are, at most 20 words")
    speaks: bool = Field(description="says words aloud in quotation marks in this passage")


class _ContextMention(_Mention):
    spoken_quote: str = Field(max_length=160, description="exact words this person speaks in the target chapter, empty otherwise")
    context_name: str = Field(max_length=60, description="name for the same person used in another supplied chapter, empty if uncertain")


def _extraction_model(max_items: int, *, contextual: bool = False) -> type[BaseModel]:
    mention_type = _ContextMention if contextual else _Mention

    class _Extraction(BaseModel):
        characters: list[mention_type] = Field(max_length=max_items)

    return _Extraction


def _batch_model(max_items: int, max_chapters: int) -> type[BaseModel]:
    class _ChapterExtraction(_extraction_model(max_items, contextual=True)):
        chapter: str

    class _BatchExtraction(BaseModel):
        chapters: list[_ChapterExtraction] = Field(max_length=max_chapters)

    return _BatchExtraction


class _Link(BaseModel):
    reason: str = Field(max_length=300)
    match: int = Field(description="candidate id that is the same person, or -1")


EXTRACT_SYSTEM = """You extract the cast of characters from a passage of a novel, for an audiobook production.
List every individual person (or individual speaking creature) who appears, speaks, or is referred to by name.
Rules:
- name: the name or title the passage uses for the person, exactly as written ("Mr. Darcy", "Lizzy", "the Colonel"). If the passage never names someone who speaks, use the role it gives them, exactly as written ("Scrooge's nephew", "the clerk", "the first gentleman").
- aliases: other names, nicknames or titles the passage uses for that SAME person, exactly as written, including a role the dialogue tags use ("said the nephew", "the Spirit"). Never pronouns, and never references relative to someone else in the scene ("his wife", "her sister", "a young man").
- Never merge two different people, even relatives who share a surname (Mr. Smith and Mrs. Smith are different people).
- Do not list groups ("the girls", "the Smiths", "the officers").
- gender: from pronouns, titles or explicit statements; "unknown" if not evident.
- age: best estimate from the passage and context (a married parent of grown children is an adult; unmarried daughters of a family seeking husbands are young adults); "unknown" only if there is no clue.
- speaks: true only if the passage contains words this person says aloud, in quotation marks, attributed to them. Being talked about or having their words reported does not count.
- Use only information in the passage. List each person once."""

NEIGHBOR_SYSTEM = EXTRACT_SYSTEM + """
The passage contains a marked TARGET and optional CONTEXT chapters. Use context to disambiguate
identities, but report only people attested in TARGET. Names, aliases, evidence phrases, and spoken
dialogue must be attested in TARGET. Do not count people who speak only in CONTEXT.
For speaks=true, give a short exact excerpt in spoken_quote; otherwise leave it empty.
Use context_name for a distinct name of the same person found only in another supplied chapter, or leave it empty."""

BATCH_SYSTEM = EXTRACT_SYSTEM + """
The passage contains labeled chapters. Use the complete passage to disambiguate identities, then
return one chapter entry for each supplied chapter ID, even if its characters list is empty.
Apply all the extraction rules independently to each chapter entry. Names, aliases, evidence phrases,
and spoken dialogue must be attested in the chapter whose ID you return.
For speaks=true, give a short exact excerpt in spoken_quote; otherwise leave it empty.
Use context_name for a distinct name of the same person found only in another supplied chapter, or leave it empty."""

LINK_SYSTEM = """You help consolidate the character list of a novel. Entries were extracted chapter by chapter,
so one person can appear under several names (e.g. "Lizzy" and "Elizabeth Bennet").
Given one ENTRY and some CANDIDATES, decide whether the entry is the same individual as one of the candidates.
- Use names, titles, family relations, roles and descriptions.
- Relatives who share a surname are different people (Mr. Bennet, Mrs. Bennet and Jane Bennet are three people).
- A bare family title ("Miss Bennet") is the same as a candidate only if the descriptions clearly point to that one person.
- If none is clearly the same person, answer -1.
Give a one-sentence reason, then the candidate id or -1."""


# ------------------------------------------------------------------ text checks

_PRONOUNS = {"he", "she", "him", "her", "his", "hers", "they", "them", "i", "me", "you", "we", "it", "who"}
_HONORIFICS = {"mr", "mrs", "miss", "ms", "master", "dr", "sir", "lady", "lord", "madam", "madame", "colonel", "captain",
               "capt", "general", "major", "lieutenant", "rev", "reverend", "the", "of", "de", "la", "le", "von", "van",
               "aunt", "uncle", "old", "young", "little", "poor", "dear", "my"}
_GROUP = re.compile(r"\b(and|&)\b|^the \w+s$", re.I)  # "Mr. and Mrs. Gardiner", "the Bennets"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("‘", "'")).strip(" ,.;:'\"“”")


def _occurs(phrase: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I) is not None


_PARTICLES = {"the", "of", "de", "du", "des", "la", "le", "van", "von", "der", "den", "da", "di", "del", "y", "d'", "o'"}
_REFERENCE_PREFIX = re.compile(r"^(my|your|his|her|our|their|this|that|poor|dear|cousin|the present)\b", re.I)


def _is_bad_name(n: str) -> bool:
    if not n or n.casefold() in _PRONOUNS or not any(ch.isupper() for ch in n):
        return True
    if _REFERENCE_PREFIX.match(n):  # "your Elizabeth", "my sister Philips", "poor Lydia": references, not names
        return True
    # Every word capitalized except name particles: rejects "the two Netherfield gentlemen", "the eldest Miss Bennet"
    if any(w[0].islower() and w.casefold() not in _PARTICLES for w in re.findall(r"[^\s.,]+", n)):
        return True
    if "'s " in n or n.endswith("'s") or "(" in n:
        return True
    return bool(_GROUP.search(n))


_ROLE = re.compile(r"^(?:the|[A-Z][\w.-]*'s)\s+(?:[A-Za-z][\w-]*\s+){0,3}[A-Za-z][\w-]*$")
_ROLE_STOP = _PRONOUNS | {"and", "one", "other", "others", "same", "two", "three", "people", "men", "women", "ladies",
                          "gentlemen", "children", "family", "party", "company", "crowd", "girls", "boys"}


def is_role(n: str) -> bool:
    """A role reference that can stand in for an unnamed speaker: "the nephew", "Scrooge's nephew",
    "the first gentleman". Not groups, not references relative to the scene ("his wife")."""
    n = _norm(n)
    if not _ROLE.match(n) or _REFERENCE_PREFIX.match(n) or "(" in n:
        return False
    words = [w.casefold() for w in re.findall(r"[A-Za-z]+", n)]
    return len(words) <= 5 and not any(w in _ROLE_STOP for w in words[1:])


def clean_aliases(name: str, aliases: list[str], text: str | None = None) -> list[str]:
    """Keep real names/titles and role references ("the nephew") that occur in `text` (when given);
    drop pronouns and scene-relative descriptions."""
    out = []
    for a in aliases:
        a = _norm(a)
        if a.casefold() == name.casefold() or (_is_bad_name(a) and not is_role(a)) or (text is not None and not _occurs(a, text)):
            continue
        if a not in out:
            out.append(a)
    return out


def core_tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z]+", name.casefold()) if t not in _HONORIFICS)


# ------------------------------------------------------------------ pooled entries


@dataclass
class Entry:
    name: str
    alias_counts: Counter = field(default_factory=Counter)
    context_names: set[str] = field(default_factory=set)
    genders: Counter = field(default_factory=Counter)
    evidence: list[str] = field(default_factory=list)
    ages: Counter = field(default_factory=Counter)
    descriptions: list[str] = field(default_factory=list)
    passages: list[tuple[str, str]] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    mentions: int = 0
    speaks: bool = False
    role: bool = False  # the name is a role ("Scrooge's nephew"), not a proper name
    scoped: bool = False  # a bare title ("the Spirit") that means someone different chapter to chapter

    @property
    def aliases(self) -> list[str]:
        return [a for a, _ in self.alias_counts.most_common()]

    def add(self, m: _Mention, chapter: str, text: str) -> None:
        self.alias_counts.update(clean_aliases(self.name, [m.name, *m.aliases], text))
        if isinstance(m, _ContextMention) and m.context_name:
            self.context_names.add(m.context_name)
        self.genders[m.gender] += 1
        if m.gender_evidence and m.gender != "unknown":
            self.evidence.append(m.gender_evidence)
        self.ages[m.age] += 1
        if m.description and m.description not in self.descriptions:
            self.descriptions.append(m.description)
        if len(self.passages) < 3 and chapter not in {cid for cid, _ in self.passages}:
            match = re.search(r"(?<!\w)" + re.escape(_norm(m.name)) + r"(?!\w)", text, re.I)
            if match:
                excerpt = " ".join(text[max(0, match.start() - 300):match.end() + 400].split())
                self.passages.append((chapter, excerpt))
        if chapter not in self.chapters:
            self.chapters.append(chapter)
        self.mentions += 1
        self.speaks |= m.speaks

    def gender(self) -> tuple[str, float]:
        known = {g: n for g, n in self.genders.items() if g != "unknown"}
        if not known:
            return "unknown", 0.0
        g, n = max(known.items(), key=lambda kv: kv[1])
        return g, n / sum(self.genders.values())

    def describe(self, label: str) -> str:
        g, _ = self.gender()
        al = f"; also called {', '.join(self.aliases[:5])}" if self.aliases else ""
        ch = ", ".join(self.chapters[:5]) + ("…" if len(self.chapters) > 5 else "")
        hints = f"; possible names from other chapters: {', '.join(sorted(self.context_names))}" if self.context_names else ""
        return f"{label} {self.name} ({g}{al}; chapters {ch}{hints}): " + " | ".join(self.descriptions[:3])


def pool(mentions: list[tuple[str, str, _Mention]]) -> list[Entry]:
    """mentions: (chapter id, window text, mention). Unverifiable or non-person names are dropped."""
    entries: dict[str, Entry] = {}
    for chapter, text, m in mentions:
        name = _norm(m.name)
        role = _is_bad_name(name) and is_role(name)
        if (_is_bad_name(name) and not role) or not _occurs(name, text):
            continue
        key = " ".join(re.findall(r"[a-z0-9]+", name.casefold()))
        scoped = bool(_BARE_TITLE.match(name))
        if scoped:  # "the Spirit" in stave two and stave three are different beings: pool per chapter
            key = f"{key}@{chapter}"
        e = entries.setdefault(key, Entry(name=name, role=role, scoped=scoped))
        e.add(m, chapter, text)
    # A role is only worth a character if that person speaks; otherwise it's just a description.
    return sorted((e for e in entries.values() if not e.role or e.speaks), key=lambda e: (-e.mentions, e.name))


_BARE_TITLE = re.compile(r"^the\s+[A-Z][a-z]+$")  # "the Spirit", "the Ghost", "the Colonel", "the Doctor"


def role_words(name: str) -> set[str]:
    """The role nouns of a role name: "Scrooge's nephew" -> {"nephew"}; "the first gentleman" -> {"first", "gentleman"}."""
    n = re.sub(r"^(the\s+|[\w.-]+'s\s+)", "", _norm(name), flags=re.I)
    return {w.casefold() for w in re.findall(r"[A-Za-z]{4,}", n)}


def confident_gender(genders: Counter) -> str | None:
    known = {g: n for g, n in genders.items() if g != "unknown"}
    if not known:
        return None
    g, n = max(known.items(), key=lambda kv: kv[1])
    return g if g in ("male", "female") and n / sum(genders.values()) >= 0.6 else None


def compatible(a: Counter, b: Counter) -> bool:
    ga, gb = confident_gender(a), confident_gender(b)
    return ga is None or gb is None or ga == gb


def candidates(entries: list[Entry], j: int, limit: int = 6) -> list[int]:
    """Earlier (more-mentioned) entries that could be the same person as entries[j]."""
    e = entries[j]
    ct = core_tokens(e.name)
    names_j = {e.name.casefold(), *(a.casefold() for a in e.aliases)}
    hinted, out = [], []
    for i in range(j):
        o = entries[i]
        if not compatible(e.genders, o.genders):
            continue
        names_i = {o.name.casefold(), *(a.casefold() for a in o.aliases)}
        if not (e.scoped and o.scoped) and (
            _context_matches(e, names_i) or _context_matches(o, names_j)
        ):
            hinted.append(i)
            continue
        if (e.scoped or o.scoped) and not set(e.chapters) & set(o.chapters):
            continue  # bare titles without corroborating context are chapter-specific
        co = core_tokens(o.name)
        if (ct and co and (ct <= co or co <= ct)) or (names_j & names_i) or _role_described(e, o) or _role_described(o, e):
            out.append(i)
    return (hinted + out)[:limit]


def _context_matches(entry: Entry, names: set[str]) -> bool:
    return any(hint.casefold() in names for hint in entry.context_names)


def _role_described(role: Entry, other: Entry) -> bool:
    """A role entry ("Scrooge's nephew") may be a named character whose description has that role ("Fred:
    Scrooge's cheerful nephew")."""
    if not role.role or other.role:
        return False
    words = role_words(role.name)
    text = " ".join(other.descriptions + other.aliases).casefold()
    return bool(words) and all(re.search(rf"\b{re.escape(w)}", text) for w in words)


def corroborated(a: Entry, b: Entry) -> bool:
    """Names alone support a link: one name's core words contain the other's ("George Wickham" ⊇ "Mr. Wickham").
    Roles never corroborate by name ("Scrooge's nephew" contains "Scrooge")."""
    if a.role or b.role:
        return False
    if (a.scoped or b.scoped) and not set(a.chapters) & set(b.chapters):
        return False
    ca, cb = core_tokens(a.name), core_tokens(b.name)
    return bool(ca and cb and (ca <= cb or cb <= ca))


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x


def clusters_from_links(entries: list[Entry], links: dict[int, int], log) -> list[list[Entry]]:
    """links: entry j -> earlier entry i. Applied in order; refused if it would mix confident genders."""
    uf = _UF(len(entries))
    genders = [Counter(e.genders) for e in entries]
    for j in sorted(links):
        ri, rj = uf.find(links[j]), uf.find(j)
        if ri == rj:
            continue
        if not compatible(genders[ri], genders[rj]):
            log(f"  refused link {entries[j].name} -> {entries[links[j]].name} (gender conflict)")
            continue
        root, child = min(ri, rj), max(ri, rj)
        uf.p[child] = root
        genders[root] += genders[child]
    groups: dict[int, list[Entry]] = {}
    for i, e in enumerate(entries):
        groups.setdefault(uf.find(i), []).append(e)
    return list(groups.values())


def canonical_name(members: list[Entry], aliases: list[str], claims: dict[str, Counter]) -> str:
    """The name the book uses most for this person (proper names before roles), upgraded to a full name
    that contains it when one is used: "Elizabeth" -> "Elizabeth Bennet", "Jane" -> "Jane Bennet". Never a
    different name ("Meg" must not become her married "Mrs. John Brooke" — that misleads attribution)."""
    uses: Counter = Counter()
    for e in members:
        uses[e.name] += e.mentions
        for a, n in e.alias_counts.items():
            if a in aliases and len(claims.get(a, {})) == 1:
                uses[a] += n
    proper = [n for n in uses if not (_is_bad_name(n) and is_role(n))] or list(uses)
    best = max(proper, key=lambda n: (uses[n], len(n)))
    core = core_tokens(best)
    fuller = [n for n in proper if core and core < core_tokens(n) and uses[n] * 10 >= uses[best]]
    return max(fuller, key=lambda n: (uses[n], -len(n))) if fuller else best


def build_characters(groups: list[list[Entry]]) -> list[Character]:
    # alias -> {group index: count}; an alias stays only with groups using it >= half as often as the top user
    claims: dict[str, Counter] = {}
    for gi, members in enumerate(groups):
        for e in members:
            claims.setdefault(e.name, Counter())[gi] += e.mentions + 1000  # an entry's own name is authoritative
            for a, n in e.alias_counts.items():
                claims.setdefault(a, Counter())[gi] += n
    keep: dict[int, list[str]] = {}
    for alias, by_group in claims.items():
        top = max(by_group.values())
        for gi, n in by_group.items():
            if n * 2 >= top:
                keep.setdefault(gi, []).append(alias)

    chars = []
    for gi, members in enumerate(groups):
        name = canonical_name(members, keep.get(gi, []), claims)
        genders, ages, evidence = Counter(), Counter(), Counter()
        descriptions, chapters = [], []
        mentions, speaks = 0, False
        for e in sorted(members, key=lambda e: -e.mentions):
            genders += e.genders
            ages += e.ages
            evidence.update(e.evidence)
            descriptions += [d for d in e.descriptions if d not in descriptions]
            chapters += [c for c in e.chapters if c not in chapters]
            mentions += e.mentions
            speaks |= e.speaks
        known = {g: n for g, n in genders.items() if g != "unknown"}
        g, conf = (max(known.items(), key=lambda kv: kv[1])[0], max(known.values()) / sum(genders.values())) if known else ("unknown", 0.0)
        known_ages = {a: n for a, n in ages.items() if a != "unknown"}
        aliases = sorted({a for a in keep.get(gi, []) if a != name}, key=lambda a: -claims[a][gi])
        chars.append(Character(
            id=slugify(name), canonical_name=name, aliases=aliases,
            gender=Gender(g), gender_confidence=round(conf, 2),
            gender_evidence=evidence.most_common(1)[0][0] if evidence else None,
            age=AgeBracket(max(known_ages.items(), key=lambda kv: kv[1])[0]) if known_ages else AgeBracket.unknown,
            description="; ".join(descriptions[:3]) or None,
            first_chapter=min(chapters) if chapters else None, chapters=sorted(chapters),
            mentions=mentions, speaks=speaks,
        ))
    # unique ids
    seen: Counter = Counter()
    for c in chars:
        seen[c.id] += 1
        if seen[c.id] > 1:
            c.id = f"{c.id}-{seen[c.id]}"
    return chars


def ambiguous(chars: list[Character]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for c in chars:
        for a in {c.canonical_name, *c.aliases}:
            owners.setdefault(a, []).append(c.id)
    return {a: ids for a, ids in sorted(owners.items()) if len(ids) > 1}


def windows(text: str, max_words: int | None, *, max_chars: int | None = None) -> list[str]:
    out, cur, n = [], [], 0
    paragraphs = []
    for paragraph in text.split("\n\n"):
        while max_chars and len(paragraph) > max_chars:
            split = paragraph.rfind(" ", 0, max_chars + 1)
            split = split if split > 0 else max_chars
            paragraphs.append(paragraph[:split])
            paragraph = paragraph[split:].lstrip()
        paragraphs.append(paragraph)
    chars = 0
    for p in paragraphs:
        w = len(p.split())
        if cur and ((max_words is not None and n + w > max_words)
                    or (max_chars is not None and chars + 2 + len(p) > max_chars)):
            out.append("\n\n".join(cur))
            cur, n = [], 0
            chars = 0
        chars += len(p) + (2 if cur else 0)
        cur.append(p)
        n += w
    if cur:
        out.append("\n\n".join(cur))
    return out


# ------------------------------------------------------------------ method


class CharactersParse(ParseMethod):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'  # the code itself; the LLM's terms are reported separately
    USES_LLM = True
    Settings = CharactersSettings
    settings: CharactersSettings

    def fingerprint(self) -> str:
        fingerprint = f"{super().fingerprint()}:{self.cfg.llm.model}:{self.cfg.llm.context_tokens}:{self.cfg.llm.min_max_tokens}"
        # Refresh books previously profiled with the per-chapter word cap in batch mode.
        return fingerprint + ":token-batch" if self.settings.extraction_mode == "batch" else fingerprint

    def check(self) -> list[tuple[bool, str]]:
        try:
            ok = self.cfg.llm.model in LLM(self.cfg.llm).models()
            return [(ok, f"llm model '{self.cfg.llm.model}' at {self.cfg.llm.base_url}")]
        except Exception as e:  # noqa: BLE001
            return [(False, f"llm endpoint {self.cfg.llm.base_url}: {e}")]

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        cl, notes = build_chapters(wd, self.settings, self.llm_for_metatext(wd))
        for n in notes:
            ctx.log(f"  note: {n}")
        ctx.log(f"  {len(cl.chapters)} chapters via {cl.method}")

        llm = LLM(self.cfg.llm, cache_dir=wd.root / "llm_cache")
        chapters = [(ch.id, wd.read_chapter_text(ch.file)) for ch in cl.chapters]
        mentions = self._extract(llm, chapters, ctx)
        entries = pool(mentions)
        ctx.log(f"  {len(mentions)} mentions -> {len(entries)} verified distinct names")

        links = self._link(llm, entries, ctx)
        groups = clusters_from_links(entries, links, ctx.log)
        chars = self._keep_locked(wd, build_characters(groups))
        chars.sort(key=lambda c: (-c.mentions, c.canonical_name))
        out = CharacterList(characters=chars, ambiguous_aliases=ambiguous(chars))
        wd.write_characters(out)

        ctx.log(f"  {len(chars)} characters ({sum(c.speaks for c in chars)} speaking); top:")
        for c in chars[:15]:
            ctx.log(f"    {c.canonical_name:<26} {c.gender.value:<7} {c.age.value:<12} {c.mentions:>3}x  aka {', '.join(c.aliases[:5])}")
        if out.ambiguous_aliases:
            ctx.log("  ambiguous aliases: " + "; ".join(f"{a} -> {', '.join(ids)}" for a, ids in list(out.ambiguous_aliases.items())[:10]))
        return out

    def _parallel(self, fn, items, ctx: Context, label: str):
        """Run fn over items concurrently; results come back in input order (determinism matters:
        later prompts are built from these results and keyed in the LLM cache)."""
        results: list = [None] * len(items)
        failed = 0
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.llm.concurrency)) as ex:
            futs = {ex.submit(fn, *it): n for n, it in enumerate(items)}
            for done, f in enumerate(as_completed(futs), 1):
                try:
                    results[futs[f]] = f.result()
                except LLMError as e:
                    failed += 1
                    ctx.log(f"  {label} failed: {e}")
                if done % 20 == 0 or done == len(items):
                    ctx.log(f"  {label}: {done}/{len(items)}")
        if failed:
            ctx.log(f"  WARNING: {failed} {label} calls failed")
        return [r for r in results if r is not None]

    def _extract(self, llm: LLM, chapters: list[tuple[str, str]], ctx: Context) -> list[tuple[str, str, _Mention]]:
        s = self.settings
        is_batch = s.extraction_mode == "batch"
        system = {"chapter": EXTRACT_SYSTEM, "neighbors": NEIGHBOR_SYSTEM, "batch": BATCH_SYSTEM}[s.extraction_mode]
        schema = (_batch_model(s.max_characters_per_window, s.batch_chapters) if is_batch
                  else _extraction_model(s.max_characters_per_window, contextual=s.extraction_mode == "neighbors"))
        output = s.batch_max_tokens if is_batch else s.max_tokens
        reserve = max(output, self.cfg.llm.min_max_tokens)
        if self.cfg.llm.dialect == "anthropic":
            from ....llm_anthropic import MIN_MAX_TOKENS

            reserve = max(reserve, MIN_MAX_TOKENS)
        limit = min(s.context_tokens, self.cfg.llm.context_tokens)
        # Include schema and framing overhead, including providers that place the schema in the prompt.
        overhead = estimate_tokens(system + str(schema.model_json_schema())) + 128
        max_chars = (limit - reserve - overhead) * 3
        if max_chars < 256:
            raise ValueError("character profiling context is too small for the prompt and response budget")

        def block(kind: str, chapter: str, text: str) -> str:
            return f'<{kind} chapter="{chapter}">\n{text}\n</{kind}>'

        jobs: list[tuple[list[tuple[str, str]], str, dict[str, str] | None]] = []
        targets: list[tuple[str, str]] = []
        user = ""
        word_limit = None if is_batch else s.window_words
        for i, (chapter, text) in enumerate(chapters):
            for focal in windows(text, word_limit, max_chars=max_chars - 128):
                if is_batch:
                    entry = block("chapter", chapter, focal)
                    if targets and (len(targets) >= s.batch_chapters or targets[-1][0] == chapter
                                    or len(user) + len(entry) + 2 > max_chars):
                        jobs.append((targets, user, None))
                        targets, user = [], ""
                    targets.append((chapter, focal))
                    user = f"{user}\n\n{entry}" if user else entry
                elif s.extraction_mode == "neighbors":
                    passage = block("target", chapter, focal)
                    neighbors = []
                    remaining = max_chars - len(passage)
                    for distance in range(1, min(s.context_chapters, len(chapters) - 1) + 1):
                        for j in (i - distance, i + distance):
                            if 0 <= j < len(chapters):
                                entry = block("context", *chapters[j])
                                if len(entry) + 2 <= remaining:
                                    neighbors.append((j, entry))
                                    remaining -= len(entry) + 2
                    before = [entry for j, entry in sorted(neighbors) if j < i]
                    after = [entry for j, entry in sorted(neighbors) if j > i]
                    context = dict(chapters[j] for j, _ in neighbors)
                    jobs.append(([(chapter, focal)], "\n\n".join([*before, passage, *after]), context))
                else:
                    jobs.append(([(chapter, focal)], focal, None))
        if targets:
            jobs.append((targets, user, None))
        if jobs:
            largest = max(estimate_tokens(system + prompt) for _, prompt, _ in jobs)
            ctx.log(f"  extract: {len(jobs)} {s.extraction_mode} requests, up to ~{largest:,} prompt tokens "
                    f"({reserve:,} reserved for response, {limit:,} total-token ceiling)")

        def validated(chapter, focal, mentions, context):
            dialogue = []
            if s.extraction_mode != "chapter" and any(m.speaks for m in mentions):
                dialogue = [text for paragraph in focal.split("\n\n")
                            for kind, text in split_paragraph(paragraph) if kind == SpanKind.dialogue]
            for mention in mentions:
                if isinstance(mention, _ContextMention):
                    quote = mention.spoken_quote.strip().strip('“”"').strip()
                    hint = _norm(mention.context_name)
                    if (_is_bad_name(hint) or _BARE_TITLE.match(hint) or _occurs(hint, focal)
                            or not any(_occurs(hint, text) for cid, text in context.items() if cid != chapter)):
                        hint = ""
                    speaks = mention.speaks and bool(quote) and any(_occurs(quote, text) for text in dialogue)
                    mention = mention.model_copy(update={"speaks": speaks, "context_name": hint})
                yield chapter, focal, mention

        def one(targets: list[tuple[str, str]], prompt: str, neighbors: dict[str, str] | None = None):
            try:
                r = llm.chat_json(system, prompt, schema, think=s.think, max_tokens=output)
                if not is_batch:
                    return list(validated(*targets[0], r.characters, neighbors or {}))
                by_chapter = {entry.chapter: entry.characters for entry in r.chapters}
                if len(r.chapters) != len(targets) or set(by_chapter) != {chapter for chapter, _ in targets}:
                    raise LLMError("character extraction returned missing, duplicate or unexpected chapter IDs")
                return [mention for chapter, focal in targets
                        for mention in validated(chapter, focal, by_chapter[chapter], dict(targets))]
            except LLMError as error:
                if not is_batch or len(targets) == 1 or not str(error).startswith(
                    ("hit max_tokens=", "invalid _BatchExtraction JSON", "character extraction returned")
                ):
                    raise
                ctx.log(f"  extract: retrying {len(targets)} chapters in smaller batches ({error.args[0].splitlines()[0]})")
                mid = len(targets) // 2
                recovered = []
                for half in (targets[:mid], targets[mid:]):
                    try:
                        recovered.extend(one(half, "\n\n".join(block("chapter", *target) for target in half)))
                    except LLMError as failed:
                        ctx.log(f"  extract failed: {failed}")
                return recovered

        return [m for batch in self._parallel(one, jobs, ctx, "extract") for m in batch]

    def _link(self, llm: LLM, entries: list[Entry], ctx: Context) -> dict[int, int]:
        asks = [(j, cands) for j in range(1, len(entries)) if (cands := candidates(entries, j, self.settings.link_candidates))]

        def ask(j: int, cands: list[int], think: bool):
            prompt = entries[j].describe("ENTRY:") + "\n\nCANDIDATES:\n" + "\n".join(entries[i].describe(f"[{i}]") for i in cands)
            output = self.settings.max_tokens_thinking if think else 1024
            room = prompt_char_budget(self.cfg.llm, LINK_SYSTEM, _Link, output, self.settings.context_tokens) - len(prompt)
            for n in range(3 if think else 1):
                for i in (j, *cands):
                    if n < len(entries[i].passages):
                        chapter, text = entries[i].passages[n]
                        source = "ENTRY" if i == j else str(i)
                        evidence = f"\n[{source}, chapter {chapter}] {text}"
                        if len(evidence) <= room:
                            prompt += evidence
                            room -= len(evidence)
            r = llm.chat_json(LINK_SYSTEM, prompt, _Link, think=think, max_tokens=output)
            return j, (r.match if r.match in cands else -1)

        quick = dict(self._parallel(lambda j, c: ask(j, c, False), asks, ctx, "link"))
        links = {j: i for j, i in quick.items() if i >= 0 and corroborated(entries[j], entries[i])}
        if self.settings.verify_links_with_thinking:
            recheck = [(j, c) for j, c in asks if j not in links]
            for j, i in self._parallel(lambda j, c: ask(j, c, True), recheck, ctx, "link (thinking)"):
                if i >= 0:
                    links[j] = i
        else:
            links.update({j: i for j, i in quick.items() if i >= 0})
        ctx.log(f"  linked {len(links)} of {len(asks)} entries with candidates")
        return links

    @staticmethod
    def _keep_locked(wd: WorkDir, chars: list[Character]) -> list[Character]:
        prev = wd.read_characters()
        if not prev:
            return chars
        locked = {c.id: c for c in prev.characters if c.locked}
        return [locked.pop(c.id, c) for c in chars] + list(locked.values())
