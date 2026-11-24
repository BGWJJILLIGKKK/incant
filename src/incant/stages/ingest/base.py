from __future__ import annotations

import json
import re

from dataclasses import dataclass, field
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from ...normalize import render_text_md, slugify
from ...schemas import BookMeta
from ...workdir import WorkDir, atomic_write, refuse_protected
from ..base import Context, Method


@dataclass
class Ingested:
    title: str
    paragraphs: list[str]  # normalized; may include '# ' headings and scene breaks
    source_format: str
    author: str | None = None
    language: str = "en"
    description: str | None = None
    cover: tuple[str, bytes] | None = None  # (extension, data)
    warnings: list[str] = field(default_factory=list)
    # Canonical address when it differs from what was given (a series' folder for one of its parts), so every
    # part of a series lands in the same book.
    source: str | None = None
    input_format: str | None = None


class IngestMethod(Method):
    @classmethod
    def accepts(cls, source: str) -> bool:
        """True if this method can handle `source`; drives auto-detection."""
        return False

    def load(self, source: str) -> Ingested:
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.meta_path, wd.text_path]

    def resource(self) -> str | None:
        return "ingest"  # one lane: sites see one book at a time, and slugs are claimed one at a time

    def run(self, ctx: Context) -> WorkDir:
        if not ctx.source:
            raise RuntimeError("ingest needs an input source")
        if "://" not in ctx.source:
            refuse_protected(ctx.source, "A book source")
        ing = self.load(ctx.source)
        for w in ing.warnings:
            ctx.log(f"  warning: {w}")
        if ctx.title and (not ing.title.strip() or ing.title == ctx.source):
            ing.title = ctx.title  # the page has no title of its own: use the container's
        ing.author = normalize_author(ing.author)
        src = ing.source or canonical_source(ctx.source)
        slug = ctx.slug or unique_slug(ctx.cfg.paths.work_dir, slugify(ing.title), src)
        wd = WorkDir.for_slug(ctx.cfg.paths.work_dir, slug)
        ctx.created = not wd.meta_path.exists()
        wd.root.mkdir(parents=True, exist_ok=True)

        ctx.wd = wd  # set early: a failure from here on can clean the directory up
        cover_name = None
        if ing.cover:
            ext, data = ing.cover
            cover_name = f"cover.{ext}"
            atomic_write(wd.root / cover_name, data)

        atomic_write(wd.source_path, json.dumps({"paragraphs": ing.paragraphs}, ensure_ascii=False) + "\n")
        atomic_write(wd.text_path, render_text_md(ing.paragraphs))
        original = ing.input_format or ing.source_format
        if ing.source_format != "url" and is_epub_file(Path(ctx.source)):
            original = "epub"
        wd.write_meta(BookMeta(slug=slug, title=ing.title, author=ing.author, language=ing.language, source=src,
                               source_format=ing.source_format, input_format=original, cover=cover_name, description=ing.description))  # type: ignore[arg-type]
        ctx.wd = wd
        ctx.log(f"  '{ing.title}' by {ing.author or 'unknown'}: {len(ing.paragraphs)} paragraphs, "
                f"cover={'yes' if cover_name else 'no'} -> {wd.root}")
        return wd


def normalize_author(author: str | None) -> str | None:
    """Library-catalog names to reading order, so one author gets one output directory:
    "Dickens, Charles, 1812-1870" -> "Charles Dickens". Several authors ("A; B", "A and B") are kept as given."""
    if not author or not (a := " ".join(author.split())):
        return None
    a = re.sub(r",?\s*\(?(?:b\.|d\.|ca\.)?\s*\d{3,4}\??\s*-\s*(?:\d{3,4}\??)?\)?$", "", a).strip(" ,")  # life dates
    parts = [p.strip() for p in a.split(",")]
    if len(parts) == 2 and all(parts) and ";" not in a and " and " not in a and len(parts[1].split()) <= 3:
        a = f"{parts[1]} {parts[0]}"
    return a or None


def canonical_source(source: str) -> str:
    """How meta.json records a source: URLs as given, files as absolute paths."""
    return source if source.startswith(("http://", "https://")) else str(Path(source).resolve())


def book_for_source(work_dir: Path, source: str) -> str | None:
    """Slug of the book ingested from `source` (canonical form), even if its title has changed since."""
    for meta in sorted(work_dir.glob("*/meta.json")):
        try:
            if BookMeta.model_validate_json(meta.read_text()).source == source:
                return meta.parent.name
        except ValueError:
            continue
    return None


def unique_slug(work_dir: Path, slug: str, source: str) -> str:
    """`slug`, or `slug-2`, `slug-3`… when a different source already owns it. Re-ingesting the same source
    reuses its work directory (whatever its slug); two different sources with the same title ("Chapter 1", "Untitled") get
    separate ones instead of overwriting each other."""
    if found := book_for_source(work_dir, source):
        return found
    n = 1
    while True:
        cand = slug if n == 1 else f"{slug}-{n}"
        meta = work_dir / cand / "meta.json"
        if not meta.exists():
            return cand
        try:
            owner = BookMeta.model_validate_json(meta.read_text()).source
        except ValueError:
            owner = None
        if owner == source:
            return cand
        n += 1



def is_epub_file(path: Path) -> bool:
    """Recognize an EPUB container even under a misleading source filename."""
    try:
        with path.open("rb") as source:
            if source.read(4) != b"PK\x03\x04":
                return False
            with ZipFile(source) as archive, archive.open("mimetype") as mime:
                return mime.read(21) == b"application/epub+zip"
    except (BadZipFile, KeyError, OSError, RuntimeError):
        return False
