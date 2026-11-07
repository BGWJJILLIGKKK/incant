"""Layout of a book's work directory. All stages go through this — no ad-hoc paths.

work/<slug>/
  meta.json            stage 1  BookMeta
  text.md              stage 1  normalized text; '# ' lines are structural headings
  cover.<ext>          stage 1  (optional)
  chapters.json        stage 2  ChapterList
  chapters/<id>.txt    stage 2  one file per chapter
  characters.json      stage 2  CharacterList
  spans/<id>.jsonl     stage 3  Span per line (per chapter, so chapters can pipeline)
  speech/<id>.json     stage 4  ChapterSpeech: spans + TTS-ready `say` text
  lexicon.toml                  optional per-book pronunciations (see normalize stage)
  cast.json                     Cast
  audio/cache/<key>.wav stage 5 content-addressed segment cache
  audio/chapters/<id>.wav stage 5 assembled, loudness-normalized chapter
  audio/manifest.json  stage 5  AudioManifest
  out/<slug>.m4b       stage 6
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .schemas import AudioManifest, BookMeta, Cast, ChapterList, ChapterSpeech, CharacterList, Span

M = TypeVar("M", bound=BaseModel)


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, mode) as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class WorkDir:
    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def for_slug(cls, work_dir: Path, slug: str) -> WorkDir:
        return cls(work_dir / slug)

    # paths
    meta_path = property(lambda s: s.root / "meta.json")
    text_path = property(lambda s: s.root / "text.md")
    chapters_path = property(lambda s: s.root / "chapters.json")
    characters_path = property(lambda s: s.root / "characters.json")
    cast_path = property(lambda s: s.root / "cast.json")
    cache_dir = property(lambda s: s.root / "audio" / "cache")
    chapter_audio_dir = property(lambda s: s.root / "audio" / "chapters")
    manifest_path = property(lambda s: s.root / "audio" / "manifest.json")
    out_dir = property(lambda s: s.root / "out")

    def spans_path(self, chapter_id: str) -> Path:
        return self.root / "spans" / f"{chapter_id}.jsonl"

    def speech_path(self, chapter_id: str) -> Path:
        return self.root / "speech" / f"{chapter_id}.json"

    def read_speech(self, chapter_id: str) -> ChapterSpeech | None:
        p = self.speech_path(chapter_id)
        return ChapterSpeech.model_validate_json(p.read_text()) if p.exists() else None

    def write_speech(self, sp: ChapterSpeech) -> None:
        self._write(self.speech_path(sp.chapter_id), sp)

    # generic json io
    def _read(self, path: Path, model: type[M]) -> M:
        return model.model_validate_json(path.read_text())

    def _write(self, path: Path, obj: BaseModel) -> None:
        atomic_write(path, obj.model_dump_json(indent=2, exclude_none=True) + "\n")

    def read_meta(self) -> BookMeta:
        return self._read(self.meta_path, BookMeta)

    def write_meta(self, m: BookMeta) -> None:
        self._write(self.meta_path, m)

    def read_chapters(self) -> ChapterList:
        return self._read(self.chapters_path, ChapterList)

    def write_chapters(self, c: ChapterList) -> None:
        self._write(self.chapters_path, c)

    def read_characters(self) -> CharacterList | None:
        p = self.characters_path
        return self._read(p, CharacterList) if p.exists() else None

    def write_characters(self, c: CharacterList) -> None:
        self._write(self.characters_path, c)

    def read_cast(self) -> Cast | None:
        p = self.cast_path
        return self._read(p, Cast) if p.exists() else None

    def write_cast(self, c: Cast) -> None:
        self._write(self.cast_path, c)

    def read_manifest(self) -> AudioManifest:
        return self._read(self.manifest_path, AudioManifest)

    def write_manifest(self, m: AudioManifest) -> None:
        self._write(self.manifest_path, m)

    def read_spans(self, chapter_id: str) -> list[Span]:
        lines = self.spans_path(chapter_id).read_text().splitlines()
        return [Span.model_validate_json(line) for line in lines if line.strip()]

    def write_spans(self, chapter_id: str, spans: list[Span]) -> None:
        body = "".join(s.model_dump_json(exclude_none=True) + "\n" for s in spans)
        atomic_write(self.spans_path(chapter_id), body)

    def read_chapter_text(self, rel: str) -> str:
        return (self.root / rel).read_text()
