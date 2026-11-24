"""Draft and publish branch of the shared ingestion pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .normalize import SCENE_BREAK
from .pipeline import ResolveError
from .schemas import BookMeta
from .stages.ingest.base import is_epub_file
from .stages.parse.chapters import _looks_like_heading
from .workdir import WorkDir, atomic_write

# Legacy converted documents and Kindle files used "epub" as their normalized source_format.
_CONVERTED = {".mobi", ".azw", ".azw3", ".prc", ".kf8", ".lit", ".pdb", ".docx", ".odt", ".rtf", ".fb2", ".html", ".htm"}


class EbookChapter(BaseModel):
    title: str = Field(min_length=1)
    paragraphs: list[str] = Field(default_factory=list)


class EbookDraft(BaseModel):
    title: str = Field(min_length=1)
    author: str | None = None
    language: str = Field(pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
    description: str | None = None
    chapters: list[EbookChapter] = Field(min_length=1)


def source_format(meta: BookMeta) -> str:
    if meta.input_format:
        return meta.input_format.casefold().lstrip(".")
    remote = meta.source.startswith(("http://", "https://"))
    if not remote and is_epub_file(Path(meta.source)):
        return "epub"
    if meta.source_format != "epub":
        return meta.source_format
    suffix = Path(urlsplit(meta.source).path if remote else meta.source).suffix.lower()
    return suffix[1:] if suffix in _CONVERTED else "epub"


def require_conversion(meta: BookMeta) -> None:
    if source_format(meta) == "epub":
        raise ResolveError("EPUB → EPUB is a same-format conversion. Choose a different input or Audiobook mode.")


def manuscript(paragraphs: list[str], title: str, *, infer: bool = True) -> list[EbookChapter]:
    explicit = any(p.startswith("# ") for p in paragraphs)
    inferred = infer and not explicit and sum(_looks_like_heading(p) for p in paragraphs) >= 2
    chapters = [EbookChapter(title="Front Matter" if explicit or inferred else title)]
    for paragraph in paragraphs:
        if (explicit and paragraph.startswith("# ")) or (inferred and _looks_like_heading(paragraph)):
            name = paragraph[2:] if explicit else paragraph.strip("* ")
            chapters.append(EbookChapter(title=name.strip() or title))
        else:
            chapters[-1].paragraphs.append(paragraph)
    if len(chapters) > 1 and not chapters[0].paragraphs:
        chapters.pop(0)
    return chapters


def draft_path(wd: WorkDir) -> Path:
    return wd.root / "ebook.json"


def read_draft(wd: WorkDir) -> EbookDraft:
    return EbookDraft.model_validate_json(draft_path(wd).read_bytes())


def save_draft(wd: WorkDir, draft: EbookDraft) -> None:
    atomic_write(draft_path(wd), draft.model_dump_json(indent=2) + "\n")


def ensure_draft(wd: WorkDir) -> EbookDraft:
    meta = wd.read_meta()
    require_conversion(meta)
    if draft_path(wd).is_file():
        return read_draft(wd)
    if wd.source_path.is_file():
        paragraphs = json.loads(wd.source_path.read_bytes())["paragraphs"]
    else:
        paragraphs = [p.strip() for p in wd.text_path.read_text().split("\n\n") if p.strip()]
    if not any(p.strip() and p != SCENE_BREAK and not p.startswith("# ") for p in paragraphs):
        raise ResolveError("The shared source contains no ebook text.")
    draft = EbookDraft(title=meta.title or "Untitled", author=meta.author, language=meta.language or "en",
                       description=meta.description, chapters=manuscript(paragraphs, meta.title or "Untitled"))
    save_draft(wd, draft)
    return draft


def output_record(wd: WorkDir) -> Path:
    return wd.root / "ebook-output.json"


def publish(wd: WorkDir, base: Path, target: Path | None = None, *, draft: EbookDraft | None = None) -> Path:
    from .epub import export

    meta = wd.read_meta()
    require_conversion(meta)
    target = target or base / "ebooks" / f"{wd.root.name}.epub"
    if target.suffix.casefold() != ".epub":
        raise ResolveError("Ebook output filename must end in .epub.")
    if not meta.source.startswith(("http://", "https://")):
        source = Path(meta.source)
        if source.resolve() == target.resolve() or (source.is_file() and target.is_file() and source.samefile(target)):
            raise ResolveError("Ebook output cannot overwrite the original source.")
    cover = wd.root / meta.cover if meta.cover else None
    if cover and (not cover.resolve().is_relative_to(wd.root.resolve()) or not cover.is_file()):
        cover = None
    try:
        path = export(draft if draft is not None else read_draft(wd), target, source=meta.source, cover=cover)
    except FileNotFoundError as exc:
        if not draft_path(wd).is_file():
            raise ResolveError("Prepare the ebook draft before publishing.") from exc
        raise
    except ValueError as exc:
        raise ResolveError(str(exc)) from exc
    atomic_write(output_record(wd), json.dumps({"files": [str(path.resolve())]}) + "\n")
    return path
