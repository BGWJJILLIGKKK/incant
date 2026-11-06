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

    def run(self, ctx: Context) -> WorkDir:
        if not ctx.source:
            raise RuntimeError("ingest needs an input source")
        ing = self.load(ctx.source)
        for w in ing.warnings:
            ctx.log(f"  warning: {w}")
        slug = ctx.slug or slugify(ing.title)
        wd = WorkDir.for_slug(ctx.cfg.paths.work_dir, slug)
        wd.root.mkdir(parents=True, exist_ok=True)

        cover_name = None
        if ing.cover:
            ext, data = ing.cover
            cover_name = f"cover.{ext}"
            atomic_write(wd.root / cover_name, data)

        atomic_write(wd.text_path, render_text_md(ing.paragraphs))
        src = ctx.source if ctx.source.startswith(("http://", "https://")) else str(Path(ctx.source).resolve())
        wd.write_meta(BookMeta(slug=slug, title=ing.title, author=ing.author, language=ing.language, source=src,
                               source_format=ing.source_format, cover=cover_name, description=ing.description))  # type: ignore[arg-type]
        ctx.wd = wd
        ctx.log(f"  '{ing.title}' by {ing.author or 'unknown'}: {len(ing.paragraphs)} paragraphs, "
                f"cover={'yes' if cover_name else 'no'} -> {wd.root}")
        return wd
