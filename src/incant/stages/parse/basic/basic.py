"""Deterministic chapter split (headings -> patterns -> single). No character profiles."""

from __future__ import annotations

from ...base import Context
from ..base import ParseMethod
from ..chapters import build_chapters
from ..metatext import MetatextSettings

__all__ = ["BasicParse"]


class BasicParse(ParseMethod):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'
    Settings = MetatextSettings
    settings: MetatextSettings

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        if wd.characters_path.exists():
            wd.characters_path.unlink()  # stale from a previous method
        cl, notes = build_chapters(wd, self.settings, self.llm_for_metatext(wd))
        for n in notes:
            ctx.log(f"  note: {n}")
        words = sum(c.word_count for c in cl.chapters)
        ctx.log(f"  {len(cl.chapters)} chapters via {cl.method}, {words:,} words")
        return cl
