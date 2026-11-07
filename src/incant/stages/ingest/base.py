from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ...normalize import render_text_md, slugify
from ...schemas import BookMeta
from ...workdir import WorkDir, atomic_write
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


@dataclass
class SourceItem:
    """One source produced by expanding a container (a bookmark file, a reading list)."""

    source: str  # a file path or URL that another ingest method accepts
    title: str | None = None  # the container's name for it; used when the source has no title of its own


class IngestMethod(Method):
    @classmethod
    def accepts(cls, source: str) -> bool:
        """True if this method can handle `source`; drives auto-detection."""
        return False

    @classmethod
    def expand(cls, source: str) -> list[SourceItem] | None:
        """Container methods return the sources inside `source` (each runs as its own book, as in a batch);
        ordinary methods return None and are run through load()."""
        return None

    def load(self, source: str) -> Ingested:
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.meta_path, wd.text_path]

    def run(self, ctx: Context) -> WorkDir:
        if not ctx.source:
            raise RuntimeError("ingest needs an input source")
        if self.expand(ctx.source) is not None:
            raise RuntimeError(f"'{ctx.source}' holds several sources; run it as a batch (pass it as the input)")
        ing = self.load(ctx.source)
        for w in ing.warnings:
            ctx.log(f"  warning: {w}")
        if ctx.title and (not ing.title.strip() or ing.title == ctx.source):
            ing.title = ctx.title  # the page has no title of its own: use the container's
        src = ctx.source if ctx.source.startswith(("http://", "https://")) else str(Path(ctx.source).resolve())
        slug = ctx.slug or unique_slug(ctx.cfg.paths.work_dir, slugify(ing.title), src)
        wd = WorkDir.for_slug(ctx.cfg.paths.work_dir, slug)
        wd.root.mkdir(parents=True, exist_ok=True)

        cover_name = None
        if ing.cover:
            ext, data = ing.cover
            cover_name = f"cover.{ext}"
            atomic_write(wd.root / cover_name, data)

        atomic_write(wd.text_path, render_text_md(ing.paragraphs))
        wd.write_meta(BookMeta(slug=slug, title=ing.title, author=ing.author, language=ing.language, source=src,
                               source_format=ing.source_format, cover=cover_name, description=ing.description))  # type: ignore[arg-type]
        ctx.wd = wd
        ctx.log(f"  '{ing.title}' by {ing.author or 'unknown'}: {len(ing.paragraphs)} paragraphs, "
                f"cover={'yes' if cover_name else 'no'} -> {wd.root}")
        return wd


def unique_slug(work_dir: Path, slug: str, source: str) -> str:
    """`slug`, or `slug-2`, `slug-3`… when a different source already owns it. Re-ingesting the same source
    reuses its work directory; two different sources with the same title ("Chapter 1", "Untitled") get
    separate ones instead of overwriting each other."""
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
