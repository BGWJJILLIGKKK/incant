"""Data contracts between pipeline stages.

Every stage reads and writes these models as JSON / JSONL inside a book's work
directory. They are the only coupling between stages: any stage can be re-run,
and any file can be hand-edited, as long as it still validates.

Bump SCHEMA_VERSION on breaking changes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

NARRATOR_ID = "narrator"


# ---------------------------------------------------------------- stage 1


class BookMeta(BaseModel):
    """Written by `ingest`. Carried through to the final m4b tags."""

    schema_version: int = SCHEMA_VERSION
    slug: str
    title: str
    author: str | None = None
    language: str = "en"
    source: str  # original path or URL
    source_format: Literal["txt", "epub", "url", "md", "pdf"]
    cover: str | None = None  # filename inside the work dir
    series: str | None = None
    series_index: float | None = None
    description: str | None = None


# ---------------------------------------------------------------- stage 2


class Chapter(BaseModel):
    id: str  # zero-padded ordinal, e.g. "0003" — stable across re-runs
    index: int
    title: str
    file: str  # relative to work dir, e.g. "chapters/0003.txt"
    word_count: int
    speak_title: bool = True  # announce the title at the start of the chapter


class ChapterList(BaseModel):
    schema_version: int = SCHEMA_VERSION
    method: Literal["headings", "pattern", "llm", "single"]
    chapters: list[Chapter]


class Gender(StrEnum):
    male = "male"
    female = "female"
    nonbinary = "nonbinary"
    unknown = "unknown"


class AgeBracket(StrEnum):
    child = "child"
    teen = "teen"
    young_adult = "young_adult"
    adult = "adult"
    elderly = "elderly"
    unknown = "unknown"


class Character(BaseModel):
    id: str  # slug of canonical name, e.g. "elizabeth-bennet"
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    gender: Gender = Gender.unknown
    gender_confidence: float = 0.0
    gender_evidence: str | None = None
    age: AgeBracket = AgeBracket.unknown
    description: str | None = None
    accent_hint: str | None = None
    first_chapter: str | None = None
    chapters: list[str] = Field(default_factory=list)  # chapters the character appears in
    mentions: int = 0  # text windows the character appears in (importance)
    speaks: bool = False  # has quoted dialogue somewhere
    line_count: int = 0  # dialogue spans attributed (filled by attribution); drives voice pooling
    locked: bool = False  # set by a human: re-runs must not overwrite
    # "profile": found by character profiling; "attribution": an unnamed speaker created while attributing
    # dialogue ("the charwoman"), scoped to one chapter and rebuilt on every attribution run
    origin: Literal["profile", "attribution"] = "profile"


class CharacterList(BaseModel):
    schema_version: int = SCHEMA_VERSION
    characters: list[Character]
    # alias -> character ids that all claim it (e.g. "Miss Bennet"); attribution must use context
    ambiguous_aliases: dict[str, list[str]] = Field(default_factory=dict)
    # First-person books: the character id who narrates ("I"), or NARRATOR_ID if unnamed. None = third person.
    narrator: str | None = None


# ---------------------------------------------------------------- stage 3


class SpanKind(StrEnum):
    narration = "narration"
    dialogue = "dialogue"


class Emotion(BaseModel):
    """How a span is delivered. Free text, as IndexTTS takes it (e.g. "quiet, trembling fear")."""

    text: str
    alpha: float = Field(0.6, ge=0.0, le=1.0)  # strength; IndexTTS sounds overacted near 1.0


class Span(BaseModel):
    """A contiguous run of chapter text with one speaker.

    Invariant: for each chapter, joining span.text within a paragraph with ""
    and paragraphs with "\\n\\n" reproduces the chapter text exactly. The LLM
    never writes `text`; it only fills `speaker` / `emotion` / `confidence`.
    """

    id: str  # "<chapter_id>-<n:05d>"
    chapter_id: str
    para: int
    kind: SpanKind
    text: str
    speaker: str | None = None  # character id; None = not yet attributed
    emotion: Emotion | None = None
    confidence: float | None = None
    locked: bool = False
    say: str | None = None  # TTS-ready text, set by the normalize stage (text itself is never changed)


class ChapterSpeech(BaseModel):
    """normalize stage output (speech/<chapter>.json): spans with `say`, plus the spoken chapter title."""

    schema_version: int = SCHEMA_VERSION
    chapter_id: str
    title_say: str
    spans: list[Span]


# ---------------------------------------------------------------- casting


class Voice(BaseModel):
    """An entry in the voice library: voices/voices.json (id -> fields) and/or voices/<id>.json."""

    id: str
    name: str | None = None
    ref_audio: str | None = None  # path relative to voices dir; only for backends that clone locally
    default_emotion: str | None = None
    # Casting tags, in the voice library's own vocabulary (e.g. gender: female/male,
    # age: teen/adult/older/neutral, tone: high/neutral/deep). Untagged = None.
    gender: str | None = None
    age: str | None = None
    tone: str | None = None
    description: str | None = None
    # Provenance — required before any distribution.
    source: str | None = None
    license: str | None = None
    consent: bool | None = None


class Cast(BaseModel):
    """character id -> voice id. Separate from attribution so recasting is free."""

    schema_version: int = SCHEMA_VERSION
    roles: dict[str, str]  # must include NARRATOR_ID
    default_voice: str | None = None  # fallback for unattributed / uncast speakers
    # What automatic casting last chose. A role equal to its auto pick is recomputed on every run (so new
    # voice tags take effect); a role you changed (or added) differs from it and is kept as-is.
    auto: dict[str, str] = Field(default_factory=dict)

    def voice_for(self, speaker: str | None) -> str | None:
        if speaker and speaker in self.roles:
            return self.roles[speaker]
        return self.default_voice or self.roles.get(NARRATOR_ID)


# ---------------------------------------------------------------- stage 4


class Segment(BaseModel):
    """One TTS call's worth of text, rendered to one cached WAV."""

    span_ids: list[str]  # consecutive same-voice spans are merged into one segment
    text: str  # TTS-ready text (markup stripped)
    voice: str | None
    emotion: Emotion | None = None
    cache_key: str
    pause_after_ms: int
    duration_ms: int | None = None


class ChapterAudio(BaseModel):
    chapter_id: str
    title: str
    file: str  # relative to work dir
    duration_ms: int
    segments: int


class AudioManifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    sample_rate: int
    chapters: list[ChapterAudio]
