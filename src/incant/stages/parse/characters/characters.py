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

from ....llm import LLM, LLMError
from ....normalize import slugify
from ....schemas import AgeBracket, Character, CharacterList, Gender
from ....workdir import WorkDir
from ...base import Context
from ..base import ParseMethod
from ..chapters import build_chapters

__all__ = ["CharactersParse"]


class CharactersSettings(BaseModel):
    window_words: int = 5000  # chapters longer than this are split for extraction
    think: bool = False  # for extraction: ~10x slower per window for little gain
    max_tokens: int = 4096
    # Linking: a fast answer is accepted when the names corroborate it ("George Wickham" ⊇ "Wickham");
    # every other question is re-asked with thinking, which is slower but far more precise.
    verify_links_with_thinking: bool = True
    # Thinking that runs past this is looping (a 9B model sometimes never converges); the link is
    # then skipped, which is the conservative outcome. Lower = less time wasted on those.
    max_tokens_thinking: int = 6144
    max_characters_per_window: int = 40


# ------------------------------------------------------------------ LLM schemas + prompts


class _Mention(BaseModel):
    name: str = Field(max_length=60, description="the person's name or title as written in the passage")
    aliases: list[str] = Field(max_length=8, description="other names, nicknames or titles used for this same person")
    gender: Literal["male", "female", "nonbinary", "unknown"]
    gender_evidence: str = Field(max_length=60, description="pronoun or phrase from the passage; empty if unknown")
    age: Literal["child", "teen", "young_adult", "adult", "elderly", "unknown"]
    description: str = Field(max_length=160, description="who they are, at most 20 words")
    speaks: bool = Field(description="says words aloud in quotation marks in this passage")


def _extraction_model(max_items: int) -> type[BaseModel]:
    class _Extraction(BaseModel):
        characters: list[_Mention] = Field(max_length=max_items)

    return _Extraction


class _Link(BaseModel):
    reason: str = Field(max_length=300)
    match: int = Field(description="candidate id that is the same person, or -1")


EXTRACT_SYSTEM = """You extract the cast of characters from a passage of a novel, for an audiobook production.
List every individual person (or individual speaking creature) who appears, speaks, or is referred to by name.
Rules:
- name: the name or title the passage uses for the person, exactly as written ("Mr. Darcy", "Lizzy", "the Colonel"). If a person is never named but speaks, use their role as written ("the housekeeper").
- aliases: other names, nicknames or titles the passage uses for that SAME person, exactly as written. Never pronouns, never descriptions ("a young man", "his wife", "her sister").
- Never merge two different people, even relatives who share a surname (Mr. Smith and Mrs. Smith are different people).
- Do not list groups ("the girls", "the Smiths", "the officers").
- gender: from pronouns, titles or explicit statements; "unknown" if not evident.
- age: best estimate from the passage and context (a married parent of grown children is an adult; unmarried daughters of a family seeking husbands are young adults); "unknown" only if there is no clue.
- speaks: true only if the passage contains words this person says aloud, in quotation marks, attributed to them. Being talked about or having their words reported does not count.
- Use only information in the passage. List each person once."""

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


def clean_aliases(name: str, aliases: list[str], text: str | None = None) -> list[str]:
    """Keep real names/titles that occur in `text` (when given); drop pronouns and descriptions."""
    out = []
    for a in aliases:
        a = _norm(a)
        if a.casefold() == name.casefold() or _is_bad_name(a) or (text is not None and not _occurs(a, text)):
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
    genders: Counter = field(default_factory=Counter)
    evidence: list[str] = field(default_factory=list)
    ages: Counter = field(default_factory=Counter)
    descriptions: list[str] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    mentions: int = 0
    speaks: bool = False

    @property
    def aliases(self) -> list[str]:
        return [a for a, _ in self.alias_counts.most_common()]

    def add(self, m: _Mention, chapter: str, text: str) -> None:
        self.alias_counts.update(clean_aliases(self.name, [m.name, *m.aliases], text))
        self.genders[m.gender] += 1
        if m.gender_evidence and m.gender != "unknown":
            self.evidence.append(m.gender_evidence)
        self.ages[m.age] += 1
        if m.description and m.description not in self.descriptions:
            self.descriptions.append(m.description)
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
        return f"{label} {self.name} ({g}{al}; chapters {ch}): " + " | ".join(self.descriptions[:3])


def pool(mentions: list[tuple[str, str, _Mention]]) -> list[Entry]:
    """mentions: (chapter id, window text, mention). Unverifiable or non-person names are dropped."""
    entries: dict[str, Entry] = {}
    for chapter, text, m in mentions:
        name = _norm(m.name)
        if _is_bad_name(name) or not _occurs(name, text):
            continue
        key = " ".join(re.findall(r"[a-z0-9]+", name.casefold()))
        e = entries.setdefault(key, Entry(name=name))
        e.add(m, chapter, text)
    return sorted(entries.values(), key=lambda e: (-e.mentions, e.name))


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
    out = []
    for i in range(j):
        o = entries[i]
        if not compatible(e.genders, o.genders):
            continue
        co = core_tokens(o.name)
        names_i = {o.name.casefold(), *(a.casefold() for a in o.aliases)}
        if (ct and co and (ct <= co or co <= ct)) or (names_j & names_i):
            out.append(i)
    return out[:limit]


def corroborated(a: Entry, b: Entry) -> bool:
    """Names alone support a link: one name's core words contain the other's ("George Wickham" ⊇ "Mr. Wickham")."""
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
    """Prefer a full name (two or more non-title words) among the members' names and this character's
    unambiguous aliases, then the most used; e.g. "Jane" + alias "Jane Bennet" -> "Jane Bennet"."""
    uses: Counter = Counter()
    for e in members:
        uses[e.name] += e.mentions
        for a, n in e.alias_counts.items():
            if a in aliases and len(claims.get(a, {})) == 1:
                uses[a] += n
    return max(uses, key=lambda n: (len(core_tokens(n)) >= 2, uses[n], len(n)))


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
            first_chapter=min(chapters) if chapters else None, mentions=mentions, speaks=speaks,
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


def windows(text: str, max_words: int) -> list[str]:
    out, cur, n = [], [], 0
    for p in text.split("\n\n"):
        w = len(p.split())
        if cur and n + w > max_words:
            out.append("\n\n".join(cur))
            cur, n = [], 0
        cur.append(p)
        n += w
    if cur:
        out.append("\n\n".join(cur))
    return out


# ------------------------------------------------------------------ method


class CharactersParse(ParseMethod):
    Settings = CharactersSettings
    settings: CharactersSettings

    def fingerprint(self) -> str:
        return super().fingerprint() + ":" + self.cfg.llm.model

    def check(self) -> list[tuple[bool, str]]:
        try:
            ok = self.cfg.llm.model in LLM(self.cfg.llm).models()
            return [(ok, f"llm model '{self.cfg.llm.model}' at {self.cfg.llm.base_url}")]
        except Exception as e:  # noqa: BLE001
            return [(False, f"llm endpoint {self.cfg.llm.base_url}: {e}")]

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        cl, notes = build_chapters(wd)
        for n in notes:
            ctx.log(f"  note: {n}")
        ctx.log(f"  {len(cl.chapters)} chapters via {cl.method}")

        llm = LLM(self.cfg.llm, cache_dir=wd.root / "llm_cache")
        jobs = [(ch.id, w) for ch in cl.chapters for w in windows(wd.read_chapter_text(ch.file), self.settings.window_words)]
        mentions = self._extract(llm, jobs, ctx)
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

    def _extract(self, llm: LLM, jobs: list[tuple[str, str]], ctx: Context) -> list[tuple[str, str, _Mention]]:
        schema = _extraction_model(self.settings.max_characters_per_window)

        def one(chapter: str, text: str):
            r = llm.chat_json(EXTRACT_SYSTEM, text, schema, think=self.settings.think, max_tokens=self.settings.max_tokens)
            return [(chapter, text, m) for m in r.characters]

        return [m for batch in self._parallel(one, jobs, ctx, "extract") for m in batch]

    def _link(self, llm: LLM, entries: list[Entry], ctx: Context) -> dict[int, int]:
        asks = [(j, cands) for j in range(1, len(entries)) if (cands := candidates(entries, j))]

        def ask(j: int, cands: list[int], think: bool):
            prompt = entries[j].describe("ENTRY:") + "\n\nCANDIDATES:\n" + "\n".join(entries[i].describe(f"[{i}]") for i in cands)
            r = llm.chat_json(LINK_SYSTEM, prompt, _Link, think=think,
                              max_tokens=self.settings.max_tokens_thinking if think else 1024)
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
