"""Rule-based speaker attribution: dialogue tags, pronouns and turn-taking. No LLM; seconds per book.

For each quotation, in order of trust:
  0. a speaker announced at the end of the previous paragraph ("…he replied:—");
  1. its dialogue tag: `"…," said Elizabeth` / `Elizabeth replied` / `said he` / `the Hatter asked`;
     a pronoun resolves to the matching-gender character named nearest before it, or in the conversation;
  2. the tag of another quotation in the same paragraph;
  3. an action beat: the paragraph's narration names exactly one character;
  4. a vocative: someone addressed by name is not the speaker, so the other party is;
  5. turn-taking: in an untagged run of dialogue paragraphs, speakers alternate (this also fills an untagged
     paragraph just before an A, B exchange).
Anything else is left unattributed (narrator's voice). Delivery comes from the tag's verb ("whispered").

Characters: uses characters.json from parse=characters when present. Without it (parse=basic), builds one from
the names in dialogue tags, with gender from titles (Mr./Mrs.) and the pronouns that follow each name, so
automatic casting still has something to go on.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from pydantic import BaseModel

from ....normalize import slugify
from ....schemas import NARRATOR_ID, Character, CharacterList, Emotion, Gender, Span, SpanKind
from ...base import Context
from ..base import AttributeMethod
from ..spans import first_person_rate, group_by_para, keep_locked, split_chapter

__all__ = ["TagsAttribute"]


class TagsSettings(BaseModel):
    emotions: bool = True  # delivery from the tag verb ("whispered" -> hushed)
    emotion_scale: float = 0.8
    turn_taking: bool = True  # alternate speakers through untagged runs of dialogue
    conversation_gap_words: int = 80  # a narration-only paragraph longer than this ends a conversation
    first_person_rate: float = 8.0  # "I/me/my" per 1000 narration words that makes a book first person


VERBS = ("said|says|asked|asks|replied|replies|answered|answers|cried|cries|exclaimed|added|continued|returned|"
         "observed|remarked|called|began|inquired|enquired|rejoined|murmured|muttered|whispered|shouted|yelled|"
         "screamed|roared|laughed|sighed|repeated|interrupted|protested|declared|demanded|retorted|snapped|growled|"
         "snarled|sobbed|stammered|gasped|pleaded|begged|responded|insisted|explained|suggested|agreed|admitted|"
         "announced|answered|breathed|chuckled|giggled|groaned|grumbled|hissed|howled|moaned|mumbled|purred|"
         "said|screeched|shrieked|sneered|whined|wondered|thought|went on|put in|broke in|cut in|called out|cried out|"
         "observed|resumed|urged|warned|ventured|persisted|faltered|concluded|thundered|bellowed|told|asked")
_VERB = rf"(?P<verb>{VERBS})"
HONORIFICS = {"mr": "male", "mister": "male", "sir": "male", "lord": "male", "master": "male", "uncle": "male",
              "king": "male", "prince": "male", "father": "male", "brother": "male", "count": "male", "duke": "male",
              "colonel": None, "captain": None, "dr": None, "doctor": None, "professor": None, "judge": None,
              "mrs": "female", "miss": "female", "ms": "female", "lady": "female", "madam": "female",
              "madame": "female", "mme": "female", "mlle": "female", "aunt": "female", "queen": "female",
              "princess": "female", "mother": "female", "sister": "female", "duchess": "female", "countess": "female"}
ROLE_GENDER = {"man": "male", "boy": "male", "gentleman": "male", "husband": "male", "father": "male",
               "son": "male", "brother": "male", "uncle": "male", "king": "male", "lad": "male", "fellow": "male",
               "woman": "female", "girl": "female", "lady": "female", "wife": "female", "mother": "female",
               "daughter": "female", "sister": "female", "aunt": "female", "queen": "female", "maid": "female"}
_HON = r"(?:Mr|Mrs|Ms|Miss|Mister|Dr|Sir|Lady|Lord|Captain|Colonel|Uncle|Aunt|Master|Madam|Madame|Mme|Mlle|Professor|Father|Mother|Sister|Brother|King|Queen|Prince|Princess|Count|Countess|Duke|Duchess|Judge|Doctor)"
_NAME = rf"(?:the\s+)?(?:(?:little|old|young|poor|dear|good|big)\s+)?(?:{_HON}\.?\s+)?[A-Z][\w'’-]*(?:\s+(?:de|van|von|la|le|[A-Z][\w'’-]*)){{0,2}}"
_DESC = r"(?:the|his|her|their|my|our|your)\s+(?:[a-z]+\s+)?[a-z][a-z'-]+"
_POSS = rf"{_NAME}['’]s\s+(?:[a-z]+\s+)?[a-z]+"
_SUBJ = rf"(?P<subj>(?:the\s+voice\s+of\s+)?{_POSS}|(?:the\s+voice\s+of\s+)?{_NAME}|{_DESC}|he|she|I|we|they)"
# after a quotation: `, said Elizabeth` / `, Elizabeth said` / ` she replied, laughing`
AFTER_VS = re.compile(rf"^[\s,;:—–-]*(?:(?:and|then)\s+)?{_VERB}\s+{_SUBJ}\b")
# right after the quotation, any past-tense verb before a name: `,” advised Jo` / `,” giggled Amy`
AFTER_ANY = re.compile(rf"^\s*[,;:—–-]+\s*(?P<verb>[a-z]+ed)\s+(?P<subj>{_NAME}|he|she)\b")
AFTER_SV = re.compile(rf"^[\s,;:—–-]*(?:(?:and|then|but)\s+)?{_SUBJ}\s+(?:\w+ly\s+)?{_VERB}\b")
# before the first quotation: `Elizabeth said:` / `Then she asked him,`
_CLAUSE = r"(?:[a-z]+(?:\s+[a-z]+){0,3},?\s+and\s+)?"  # "shook his head and said"
BEFORE_SV = re.compile(rf"\b{_SUBJ}\s+(?:\w+ly\s+)?{_CLAUSE}{_VERB}(?:\s+\w+){{0,4}}[\s,:—–-]*$")
# a paragraph ending `… as he replied:—` introduces the next paragraph's speech
TRAILING = re.compile(rf"\b{_SUBJ}\s+(?:\w+ly\s+)?{_CLAUSE}{_VERB}(?:\s+\w+){{0,4}}\s*[:—–-]+\s*$")
# a narration sentence's subject: `He made no motion…` / `The Hatter opened his eyes…`
_SENT_SUBJ = re.compile(rf"^[\s,;:—–-]*(?:(?:and|then|but|here|now)\s+)?(?P<subj>{_NAME}|he|she|I)\b(?!\s*[”\"])")
NOT_NAMES = {"the", "but", "and", "then", "when", "yes", "no", "oh", "well", "it", "this", "that", "there", "here",
             "now", "so", "what", "why", "how", "who", "if", "as", "at", "in", "on", "for", "his", "her", "they",
             "she", "he", "we", "you", "i", "a", "an", "all", "after", "before", "still", "yet", "perhaps", "indeed",
             "ah", "dear", "good", "o", "nay", "aye", "god", "heaven", "lord", "sir", "madam", "mother", "father"}
SENTENCE_WORDS = {"presently", "suddenly", "again", "once", "finally", "at", "soon", "meanwhile", "instantly",
                  "immediately", "afterwards", "later", "next", "thus", "therefore", "accordingly", "besides",
                  "whereupon", "hereupon", "upon", "said", "cried", "asked", "replied", "answered", "laughing",
                  "smiling", "turning", "looking", "seeing", "hearing", "nodding", "only", "even", "just", "first"}
EMOTION = {"whispered": ("hushed, whispering", 0.5), "breathed": ("hushed, whispering", 0.4),
           "murmured": ("soft, murmuring", 0.4), "muttered": ("muttering, low", 0.4), "mumbled": ("mumbling, low", 0.4),
           "shouted": ("shouting, loud", 0.6), "yelled": ("shouting, loud", 0.6), "screamed": ("screaming, frantic", 0.7),
           "roared": ("roaring, loud", 0.7), "bellowed": ("bellowing, loud", 0.7), "thundered": ("thundering, angry", 0.7),
           "shrieked": ("shrieking, shrill", 0.7), "screeched": ("shrieking, shrill", 0.7), "cried out": ("crying out", 0.5),
           "exclaimed": ("exclaiming, emphatic", 0.4), "laughed": ("laughing, amused", 0.5), "chuckled": ("amused, chuckling", 0.4),
           "giggled": ("giggling", 0.5), "sobbed": ("tearful, sobbing", 0.6), "sighed": ("sighing, weary", 0.4),
           "groaned": ("groaning, pained", 0.5), "moaned": ("moaning, miserable", 0.5), "gasped": ("breathless, shocked", 0.5),
           "stammered": ("nervous, stammering", 0.5), "faltered": ("hesitant, faltering", 0.4),
           "snapped": ("sharp, irritated", 0.5), "retorted": ("sharp retort", 0.4), "growled": ("growling, angry", 0.6),
           "snarled": ("snarling, angry", 0.6), "hissed": ("hissing, angry", 0.6), "sneered": ("sneering, contemptuous", 0.5),
           "pleaded": ("pleading", 0.5), "begged": ("pleading", 0.5), "whined": ("whining", 0.5), "purred": ("purring, sly", 0.5),
           "howled": ("howling, distraught", 0.6), "grumbled": ("grumbling, annoyed", 0.4)}
PERSON_NOUNS = set(ROLE_GENDER) | {
    "nephew", "niece", "cousin", "child", "grandmother", "grandfather", "widow", "clerk", "servant", "housekeeper",
    "butler", "landlord", "landlady", "master", "mistress", "doctor", "captain", "colonel", "inspector", "constable",
    "governess", "nurse", "coachman", "footman", "porter", "lawyer", "partner", "stranger", "other", "visitor",
    "guest", "host", "hostess", "officer", "driver", "guard", "soldier", "sailor", "seaman", "priest", "parson",
    "friend", "companion", "speaker", "voice", "newcomer", "youth", "fellow", "gentleman", "lady", "old", "one",
    "mate", "pirate", "count", "countess", "professor", "detective", "prisoner", "keeper", "farmer", "witch",
    "wizard", "teacher", "schoolmaster", "student", "boy", "girl", "man", "woman"}
_SEGMENTS = re.compile(r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bSt)[.;]")
_PRONOUN_AFTER = re.compile(r"\b(he|him|his|himself|she|her|hers|herself)\b", re.I)


def strip_article(s: str) -> str:
    """"the little Hatter" -> "Hatter"."""
    s = re.sub(r"^the\s+voice\s+of\s+", "", s.strip(), flags=re.I)
    s = re.sub(r"^(?:the|his|her|their|my|our|your)\s+", "", s.strip(), flags=re.I)
    return re.sub(r"^(?:little|old|young|poor|dear|good|big)\s+(?=[A-Z])", "", s)


def honorific_gender(name: str) -> str | None:
    m = re.match(rf"^{_HON}\b", name)
    return HONORIFICS.get(m.group(0).lower().rstrip(".")) if m else None


@dataclass
class Tag:
    subj: str
    verb: str


def clean_subject(subj: str) -> str:
    """Drop sentence-opening words a capitalized-name pattern swallows: "Presently Tom" -> "Tom"."""
    toks = subj.split()
    if toks and toks[0].islower():  # a description: "his lady to (him)" -> "his lady"
        while len(toks) > 2 and toks[-1] in ("to", "of", "in", "at", "with", "and", "as", "for", "from", "on", "upon"):
            toks = toks[:-1]
        return " ".join(toks)
    while len(toks) > 1 and (toks[0].casefold() in NOT_NAMES or toks[0].casefold() in SENTENCE_WORDS
                             or (toks[0].endswith("ly") and toks[0][0].isupper())):
        toks = toks[1:]
    return " ".join(toks)


def find_tag(narr_after: str | None, narr_before: str | None) -> Tag | None:
    if narr_after:
        head = narr_after[:160]
        for rx in (AFTER_VS, AFTER_SV, AFTER_ANY):
            if (m := rx.search(head)) and _plausible(m.group("subj")):
                return Tag(clean_subject(m.group("subj")), m.group("verb"))
    if narr_before:
        tail = narr_before[-160:]
        if (m := BEFORE_SV.search(tail)) and _plausible(m.group("subj")):
            return Tag(clean_subject(m.group("subj")), m.group("verb"))
    return None


def _plausible(subj: str) -> bool:
    first = strip_article(subj).split()[0].casefold().rstrip(".") if strip_article(subj) else ""
    return subj in ("he", "she", "I", "we", "they") or first not in NOT_NAMES or bool(re.match(_DESC, subj))


class Roster:
    """Name/alias -> character id, plus genders, for resolving tag subjects."""

    def __init__(self, chars: CharacterList):
        self.chars = chars
        self.by_id = {c.id: c for c in chars.characters}
        self.index: dict[str, set[str]] = defaultdict(set)
        for c in chars.characters:
            for n in (c.canonical_name, *c.aliases):
                self.index[n.casefold()].add(c.id)
                self.index[strip_article(n).casefold()].add(c.id)
        self.last_token: dict[str, set[str]] = defaultdict(set)
        for c in chars.characters:
            for n in (c.canonical_name, *c.aliases):
                toks = strip_article(n).split()
                if toks and not honorific_gender(n):
                    self.last_token[toks[-1].casefold()].add(c.id)
        # Surface forms for finding names in text: case-sensitive (so "Jo" isn't "jo"), any case for the first letter.
        forms = {strip_article(n) if n[:4].lower() == "the " else n
                 for c in chars.characters for n in (c.canonical_name, *c.aliases)}
        self.forms = sorted((f for f in forms if len(f) >= 2 and len(self.index.get(f.casefold(), ())) == 1),
                            key=len, reverse=True)
        self.form_rx = {f: re.compile(rf"(?<![\w])(?i:{re.escape(f[0])}){re.escape(f[1:])}(?![\w])") for f in self.forms}

    def gender(self, cid: str | None) -> str | None:
        c = self.by_id.get(cid or "")
        return c.gender.value if c and c.gender in (Gender.male, Gender.female) else None

    def resolve(self, subj: str, recent: list[str]) -> str | None:
        """A name or description -> character id; ambiguous names go to the most recent candidate."""
        for key in (subj.casefold(), strip_article(subj).casefold()):
            ids = self.index.get(key)
            if ids:
                return self._pick(ids, recent)
        toks = strip_article(subj).split()
        if len(toks) == 1 and (ids := self.last_token.get(toks[0].casefold())):
            return self._pick(ids, recent)
        return None

    def _pick(self, ids: set[str], recent: list[str]) -> str:
        if len(ids) == 1:
            return next(iter(ids))
        for r in reversed(recent):
            if r in ids:
                return r
        return max(ids, key=lambda i: self.by_id[i].mentions if i in self.by_id else 0)

    def by_role(self, noun: str, related: list[str]) -> str | None:
        """A character described by a role ("nephew", "wife", "stranger"): from the profiles' descriptions,
        preferring one whose description names a character from `related` ("Scrooge's nephew")."""
        alias_rx = re.compile(rf"\b{re.escape(noun)}$", re.I)
        in_alias = [c for c in self.chars.characters if any(alias_rx.search(a) for a in (c.canonical_name, *c.aliases))]
        if noun not in PERSON_NOUNS and not in_alias:
            return None  # "his head", "the door": not a person
        # The role must be the character's own: a description segment that starts with it ("Wife of Mr. Bennet",
        # "Scrooge's nephew who…"), not one that mentions it ("friend of Scrooge's nephew").
        head = re.compile(rf"^(?:(?:a|an|the|his|her|their)\s+)?(?:[\w’'.-]+['’]s?\s+)?(?:\w+\s+){{0,2}}?{re.escape(noun)}\b", re.I)
        names = {r: [n for n in (self.by_id[r].canonical_name, *self.by_id[r].aliases) if len(n) > 2]
                 for r in related if r in self.by_id}
        cands: dict[str, list[str]] = {c.id: [] for c in in_alias}
        for c in self.chars.characters:
            for seg in _SEGMENTS.split(c.description or ""):
                seg = seg.strip()
                if head.match(seg) and " of " not in head.match(seg).group(0):
                    cands.setdefault(c.id, []).append(seg.replace("’", "'").casefold())
        if not cands:
            return None
        for r in related:  # whose nephew / wife: the role named with someone in the scene
            for cid, segs in cands.items():
                if cid != r and any(n.replace("’", "'").casefold() in seg for seg in segs for n in names.get(r, [])):
                    return cid
        return next(iter(cands)) if len(cands) == 1 else None

    def mentioned(self, text: str) -> list[str]:
        """Character ids named in `text`, in order of position."""
        found: list[tuple[int, str]] = []
        taken: list[tuple[int, int]] = []
        for f in self.forms:
            if f[1:] not in text:
                continue
            for m in self.form_rx[f].finditer(text):
                if any(a < m.end() and m.start() < b for a, b in taken):
                    continue
                found.append((m.start(), next(iter(self.index[f.casefold()]))))
                taken.append((m.start(), m.end()))
        return [cid for _, cid in sorted(found)]


def roster_from_names(names: list[str], counts: Counter, text: str, speaking: set[str], origin: str) -> list[Character]:
    """One character per person from name strings: bare first names / surnames merged into the one longer name
    they belong to ("Elizabeth" -> "Elizabeth Bennet"), gender from titles, role nouns or the pronouns that
    follow the name in `text`; mentions from `counts`; speaks if any alias is in `speaking`."""
    groups: dict[str, set[str]] = {}
    for n in sorted(names, key=lambda x: (-len(x.split()), -counts[x])):
        toks = re.sub(rf"^{_HON}\.?\s+", "", n).split()
        target = None
        if len(n.split()) == 1:
            owners = [g for g in groups if n in re.sub(rf"^{_HON}\.?\s+", "", g).split() and not honorific_gender(g)]
            hon_owners = [g for g in groups if n == re.sub(rf"^{_HON}\.?\s+", "", g)]
            cand = owners or hon_owners
            if len(cand) == 1:
                target = cand[0]
        if target:
            groups[target].add(n)
        elif toks:
            groups[n] = {n}
    chars = []
    for canon, aliases in groups.items():
        g = honorific_gender(canon)
        if g is None and canon[0].islower():
            g = ROLE_GENDER.get(canon.split()[-1])
        votes = Counter()
        if g is None:
            for a in aliases:
                for m in re.finditer(rf"\b{re.escape(a)}\b", text):
                    if p := _PRONOUN_AFTER.search(text, m.end(), m.end() + 60):
                        votes["female" if p.group(1).lower().startswith(("s", "her")) else "male"] += 1
            total = sum(votes.values())
            if total >= 2:
                top, k = votes.most_common(1)[0]
                g = top if k / total >= 0.7 else None
        lines = sum(counts[a] for a in aliases)
        chars.append(Character(id=slugify(canon) or "character", canonical_name=canon,
                               aliases=sorted(aliases - {canon}), gender=Gender(g) if g else Gender.unknown,
                               gender_confidence=0.9 if g and not votes else (0.6 if g else 0.0),
                               gender_evidence="title" if g and not votes else (f"pronouns {dict(votes)}" if votes else None),
                               mentions=lines, speaks=bool(aliases & speaking), origin=origin))
    seen: set[str] = set()
    for c in chars:  # unique ids
        base, n = c.id, 2
        while c.id in seen:
            c.id, n = f"{base}-{n}", n + 1
        seen.add(c.id)
    return chars


def tag_name_counts(book: dict[str, list[list[Span]]]) -> Counter:
    """How often each name (or role, lowercased) is the subject of a dialogue tag."""
    counts: Counter[str] = Counter()
    for paras in book.values():
        for para in paras:
            if para[-1].kind is SpanKind.narration and (m := TRAILING.search(para[-1].text[-200:])):
                key = strip_article(clean_subject(m.group("subj")))
                if key and key[0].isupper() and _plausible(key):
                    counts[key] += 1
            for i, s in enumerate(para):
                if s.kind is not SpanKind.dialogue:
                    continue
                after = next((x.text for x in para[i + 1:i + 2] if x.kind is SpanKind.narration), None)
                before = para[i - 1].text if i and para[i - 1].kind is SpanKind.narration else None
                t = find_tag(after, before)
                if t and t.subj not in ("he", "she", "I", "we", "they"):
                    key = strip_article(t.subj)
                    if key and not re.search(r"['’]s\s", key):  # possessive roles ("Scrooge's nephew") resolve later
                        counts[key if key[0].isupper() else key.casefold()] += 1
    return counts


def build_roster(book: dict[str, list[list[Span]]], log=None) -> CharacterList:
    """Characters from dialogue-tag names (no characters.json): one per person, gender from titles/pronouns."""
    counts = tag_name_counts(book)
    names = [n for n, k in counts.items()
             if n[0].isupper() or (k >= 2 and n.split()[-1] in PERSON_NOUNS and n.split()[-1] not in ("other", "one"))]
    text = " ".join(s.text for paras in book.values() for para in paras for s in para if s.kind is SpanKind.narration)
    chars = roster_from_names(names, counts, text, speaking=set(names), origin="attribution")
    if log:
        log(f"  no characters.json: {len(chars)} speakers found in dialogue tags")
    return CharacterList(characters=chars)


class TagsAttribute(AttributeMethod):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'
    Settings = TagsSettings
    settings: TagsSettings
    trace: list | None = None  # set to [] to record (first quote id, rule, speaker, per-quote tags) per paragraph

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        chapters = wd.read_chapters().chapters
        only = ctx.chapters
        book: dict[str, list[list[Span]]] = {}
        all_spans: dict[str, list[Span]] = {}
        for ch in chapters:
            spans = split_chapter(ch.id, wd.read_chapter_text(ch.file))
            all_spans[ch.id] = spans
            book[ch.id] = group_by_para(spans)

        chars = wd.read_characters()
        profiled = chars is not None and any(c.origin == "profile" for c in chars.characters)
        if not profiled:
            chars = build_roster(book, ctx.log)
        else:
            chars.characters = [c for c in chars.characters if c.origin == "profile"]
        narr_words = [s for spans in all_spans.values() for s in spans][:20000]
        first_person = first_person_rate(narr_words) >= self.settings.first_person_rate
        me = chars.narrator if chars.narrator else (NARRATOR_ID if first_person else None)
        roster = Roster(chars)

        tagged = total = 0
        counts: Counter[str] = Counter()
        for ch in chapters:
            if only is not None and ch.id not in only:
                for s in wd.read_spans(ch.id) if wd.spans_path(ch.id).exists() else []:
                    if s.kind is SpanKind.dialogue and s.speaker:
                        counts[s.speaker] += 1
                continue
            spans = all_spans[ch.id]
            t, n = self._chapter(book[ch.id], roster, me)
            tagged, total = tagged + t, total + n
            keep_locked(wd, ch.id, spans)
            for s in spans:
                if s.kind is SpanKind.dialogue and s.speaker:
                    counts[s.speaker] += 1
            wd.write_spans(ch.id, spans)
        for c in chars.characters:
            c.line_count = counts.get(c.id, 0)
            c.speaks = c.speaks or c.line_count > 0
        if first_person and not chars.narrator:
            chars.narrator = NARRATOR_ID
        wd.write_characters(chars)
        done = sum(1 for spans in all_spans.values() for s in spans if s.kind is SpanKind.dialogue and s.speaker)
        ctx.log(f"  {done:,}/{total:,} quotations attributed ({tagged:,} from dialogue tags), "
                f"{sum(1 for c in chars.characters if c.line_count)} speakers" + (" [first person]" if first_person else ""))
        return {"attributed": done, "quotes": total}

    def _chapter(self, paras: list[list[Span]], roster: Roster, me: str | None) -> tuple[int, int]:
        st = self.settings
        recent: list[str] = []  # characters named in narration or speaking, most recent last
        convo: list[str | None] = []  # speakers of consecutive dialogue paragraphs
        chapter_text = " ".join(s.text for p in paras for s in p if s.kind is SpanKind.narration)
        weight = Counter(roster.mentioned(chapter_text))  # who this chapter is about
        tagged = total = 0
        pending: str | None = None  # speaker announced at the end of the previous paragraph ("he replied:—")

        def note(cid: str | None):
            if cid:
                if cid in recent:
                    recent.remove(cid)
                recent.append(cid)

        def by_gender(want: str, here: list[str], exclude: set[str]) -> str | None:
            last_speaker = next((x for x in reversed(convo) if x), None)
            chapter_pool = [c for c, _ in weight.most_common()]
            for pool in (list(reversed(here)), [x for x in reversed(convo) if x], list(reversed(recent)), chapter_pool):
                cands = [c for c in dict.fromkeys(pool) if roster.gender(c) == want and c not in exclude]
                if len(cands) > 1 and pool is not here:
                    cands = [c for c in cands if c != last_speaker] or cands  # back-and-forth: the other one
                if cands:
                    return cands[0]
            return None

        def resolve(subj: str, here: list[str], exclude: set[str] = frozenset()) -> str | None:
            if subj in ("I", "we"):
                return me
            if subj == "they":
                return None
            if subj in ("he", "she", "He", "She"):
                return by_gender("male" if subj.lower() == "he" else "female", here, exclude)
            if m := re.match(rf"^({_NAME})['’]s\s+(?:[a-z]+\s+)?([a-z]+)$", subj):  # "Scrooge's nephew"
                owner = roster.resolve(m.group(1), recent)
                return roster.by_role(m.group(2), [owner] if owner else [])
            cid = roster.resolve(subj, recent)
            if cid is None and re.match(_DESC, subj):  # "his wife", "the stranger", "the other"
                noun = subj.split()[-1].lower()
                if noun == "other":
                    return other(exclude | {x for x in convo[-1:] if x})
                if subj.lower().startswith("his ") and noun in ("lady", "woman", "spouse"):
                    noun = "wife"  # "said his lady"
                # whose wife/nephew: anyone in the scene, the person addressed included ("My dear Mr. Bennet," said his wife)
                related = [*exclude, *reversed(here), *(x for x in reversed(convo) if x)]
                cid = roster.by_role(noun, list(dict.fromkeys(related))[:4])
                if cid is None and (g := ROLE_GENDER.get(noun)):
                    cid = by_gender(g, here, exclude)
            return cid if cid not in exclude else None

        def other(exclude: set[str]) -> str | None:
            """The conversation's other party."""
            for x in reversed(convo):
                if x and x not in exclude:
                    return x
            if me and me != NARRATOR_ID and me not in exclude:
                return me
            for x in reversed(recent):
                if x not in exclude:
                    return x
            return None

        def vocatives(quote: str) -> set[str]:
            out = set()
            for cid in roster.mentioned(quote):
                c = roster.by_id[cid]
                for n in (c.canonical_name, *c.aliases):
                    if re.search(rf"(?:^[“\"]?|[,!?.]\s+|\b(?:dear|Say|Oh|O|Well|Yes|No),?\s+){re.escape(n)}\s*(?:[,!?.]|[”\"]$)", quote):
                        out.add(cid)
            return out

        for para in paras:
            quotes = [i for i, s in enumerate(para) if s.kind is SpanKind.dialogue]
            narr = [s.text for s in para if s.kind is SpanKind.narration]
            narration = " ".join(narr)
            last = para[-1]
            trailing = TRAILING.search(last.text[-200:]) if last.kind is SpanKind.narration else None
            if not quotes:
                for cid in roster.mentioned(narration):
                    note(cid)
                if len(narration.split()) > st.conversation_gap_words or not narration.strip():
                    convo.clear()
                pending = resolve(clean_subject(trailing.group("subj")), roster.mentioned(last.text)) if trailing else None
                continue
            total += len(quotes)
            here = roster.mentioned(narration)
            addressed = set().union(*(vocatives(para[i].text) for i in quotes))
            who: dict[int, tuple[str | None, str | None]] = {}
            for i in quotes:
                after = para[i + 1].text if i + 1 < len(para) and para[i + 1].kind is SpanKind.narration else None
                if after and trailing and para[i + 1] is last and trailing.start() < 3:
                    after = None  # "…” he replied:—" belongs to the next paragraph
                before = para[i - 1].text if i == quotes[0] and i and para[i - 1].kind is SpanKind.narration else None
                tag = find_tag(after, before)
                if tag:
                    upto = " ".join(x.text for x in para[:i + 2] if x.kind is SpanKind.narration)
                    if cid := resolve(tag.subj, roster.mentioned(upto), addressed):
                        who[i] = (cid, tag.verb)
            next_speaker = resolve(clean_subject(trailing.group("subj")), here) if trailing else None
            main, why = None, ""
            if who:
                tagged += len(who)
                main = next(iter(who.values()))[0]
            elif pending:
                main = pending
            elif next_speaker:
                main = other({next_speaker})
            else:
                lead = para[quotes[0] - 1].text if quotes[0] and para[quotes[0] - 1].kind is SpanKind.narration else ""
                after = para[quotes[0] + 1].text if quotes[0] + 1 < len(para) and para[quotes[0] + 1].kind is SpanKind.narration else ""
                lead_sent = re.split(r"(?<=[.!?])\s+", lead.strip())[-1] if lead.strip() else ""
                for sent in (lead_sent, after):
                    if sent and (m := _SENT_SUBJ.search(sent)):
                        main = resolve(clean_subject(m.group("subj")), roster.mentioned(sent), addressed)
                        if main:
                            why = f"beat:{m.group('subj')}"
                            break
                if main is None:
                    if addressed:
                        main, why = other(addressed), f"vocative:{sorted(addressed)}"
                    elif st.turn_taking and len(convo) >= 2 and convo[-1] and convo[-2] and convo[-1] != convo[-2]:
                        main, why = convo[-2], "turns"
            if self.trace is not None:
                rule = ("tag" if who else "pending" if pending else "before-trailing" if next_speaker else why or "none")
                self.trace.append((para[quotes[0]].id, rule, main, {k: v[0] for k, v in who.items()}))
            for i in quotes:
                cid, verb = who.get(i, (main, None))
                s = para[i]
                if s.locked:
                    continue
                s.speaker = cid
                s.emotion = None
                if cid and verb and st.emotions and verb in EMOTION:
                    text, k = EMOTION[verb]
                    s.emotion = Emotion(text=text, alpha=round(min(0.8, k * st.emotion_scale), 2))
            for cid in here:
                note(cid)
            note(main)
            convo.append(main)
            pending = next_speaker

        if st.turn_taking:
            self._fill_backwards(paras)
        return tagged, total

    @staticmethod
    def _fill_backwards(paras: list[list[Span]]) -> None:
        """An untagged paragraph just before an A, B exchange is B's turn."""
        dlg = [(k, p) for k, p in enumerate(paras) if any(s.kind is SpanKind.dialogue for s in p)]
        for j in range(len(dlg) - 3, -1, -1):
            k, p = dlg[j]
            (k1, p1), (k2, p2) = dlg[j + 1], dlg[j + 2]
            if k1 != k + 1 or k2 != k1 + 1:
                continue
            q = [s for s in p if s.kind is SpanKind.dialogue]
            if all(s.speaker for s in q):
                continue
            a = next((s.speaker for s in p1 if s.kind is SpanKind.dialogue and s.speaker), None)
            b = next((s.speaker for s in p2 if s.kind is SpanKind.dialogue and s.speaker), None)
            if a and b and a != b:
                for s in q:
                    if not s.speaker and not s.locked:
                        s.speaker = b
