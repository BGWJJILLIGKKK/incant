"""Stage 2a: deterministic chapter splitting of text.md.

Order of preference:
  1. '# ' headings emitted by ingest (EPUB structure)
  2. heading-like paragraphs matching common patterns ("Chapter 12", "CHAPTER XII.", "Prologue")
  3. the whole book as one chapter
An LLM fallback for books with no recognisable structure is future work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...normalize import SCENE_BREAK
from ...schemas import Chapter, ChapterList
from ...workdir import WorkDir, atomic_write

_NUM_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred"
)
_ROMAN = r"[IVXLCDM]+"
_NUMBER = rf"(\d+|{_ROMAN}|(?:(?:{_NUM_WORDS})[\s-]*)+)"
_PATTERN = re.compile(
    rf"^(?:(?:chapter|book|part|volume|letter)\s+{_NUMBER}\b.*"
    rf"|(?:prologue|epilogue|introduction|preface|foreword|afterword|interlude)\b.*"
    rf"|{_ROMAN}\.?|\d{{1,3}}\.?)$",
    re.I,
)
_DIVISION = re.compile(rf"^(?:part|book|volume)\s+{_NUMBER}\b", re.I)
MAX_HEADING_LEN = 80
MIN_FRONT_MATTER_WORDS = 40  # text before the first heading shorter than this is title-page noise


@dataclass
class _Raw:
    title: str
    paras: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paras if p != SCENE_BREAK)


def _looks_like_heading(p: str) -> bool:
    return len(p) <= MAX_HEADING_LEN and bool(_PATTERN.match(p.strip("* ").strip()))


def _split(paragraphs: list[str], is_heading, title_of) -> list[_Raw]:
    chunks: list[_Raw] = [_Raw(title="")]
    for p in paragraphs:
        if is_heading(p):
            chunks.append(_Raw(title=title_of(p)))
        else:
            chunks[-1].paras.append(p)
    return chunks


def _cleanup(chunks: list[_Raw]) -> tuple[list[_Raw], list[str]]:
    notes: list[str] = []
    front, rest = chunks[0], chunks[1:]
    if front.words >= MIN_FRONT_MATTER_WORDS:
        front.title = "Front Matter"
        rest.insert(0, front)
    elif front.paras:
        notes.append(f"dropped {front.words} words of title-page text before first heading")

    # A run of 3+ empty headings is a table of contents, not chapters.
    out: list[_Raw] = []
    i = 0
    while i < len(rest):
        j = i
        while j < len(rest) and rest[j].words == 0:
            j += 1
        run = j - i
        if run >= 3:
            notes.append(f"dropped table of contents ({run} empty headings starting '{rest[i].title}')")
            i = j
            continue
        # 1-2 empty headings: fold real divisions ("Part One") into the next chapter's title;
        # drop the rest ("Contents", "by Arthur Conan Doyle").
        if run and j < len(rest):
            keep = [c.title for c in rest[i:j] if _DIVISION.match(c.title)]
            dropped = [c.title for c in rest[i:j] if not _DIVISION.match(c.title)]
            if dropped:
                notes.append(f"dropped empty heading(s): {', '.join(repr(t) for t in dropped)}")
            rest[j].title = ": ".join(keep + [rest[j].title])
            i = j
            continue
        if run:  # trailing empty headings
            notes.append(f"dropped {run} empty trailing heading(s)")
            break
        out.append(rest[i])
        i += 1
    return out, notes


def split_chapters(text: str) -> tuple[list[_Raw], str, list[str]]:
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    paragraphs = [p.strip() for p in paragraphs]

    if sum(p.startswith("# ") for p in paragraphs) >= 1:
        chunks = _split(paragraphs, lambda p: p.startswith("# "), lambda p: p[2:].strip())
        method = "headings"
    elif sum(_looks_like_heading(p) for p in paragraphs) >= 2:
        chunks = _split(paragraphs, _looks_like_heading, lambda p: p.strip("* ").strip())
        method = "pattern"
    else:
        return [_Raw(title="", paras=paragraphs)], "single", []

    chunks, notes = _cleanup(chunks)
    if not chunks:
        return [_Raw(title="", paras=[p for p in paragraphs if not p.startswith("# ")])], "single", notes
    return chunks, method, notes


def _strip_edge_breaks(paras: list[str]) -> list[str]:
    while paras and paras[0] == SCENE_BREAK:
        paras = paras[1:]
    while paras and paras[-1] == SCENE_BREAK:
        paras = paras[:-1]
    return paras


def build_chapters(wd: WorkDir) -> tuple[ChapterList, list[str]]:
    meta = wd.read_meta()
    chunks, method, notes = split_chapters(wd.text_path.read_text())
    chapters: list[Chapter] = []
    for i, ch in enumerate(chunks, start=1):
        cid = f"{i:04d}"
        rel = f"chapters/{cid}.txt"
        paras = [p.removeprefix("\\") for p in _strip_edge_breaks(ch.paras)]
        atomic_write(wd.root / rel, "\n\n".join(paras) + "\n")
        title = ch.title or (meta.title if method == "single" else f"Chapter {i}")
        chapters.append(Chapter(id=cid, index=i, title=title, file=rel, word_count=ch.words))
    cl = ChapterList(method=method, chapters=chapters)  # type: ignore[arg-type]
    wd.write_chapters(cl)
    return cl, notes
