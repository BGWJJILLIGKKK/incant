"""No respelling: only strips markup and quote marks (and applies the lexicon)."""

from __future__ import annotations

from ....schemas import SpanKind
from ....textprep import speakable
from ..base import NormalizeMethod

__all__ = ["NoNormalize"]


class NoNormalize(NormalizeMethod):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'
    def say(self, text: str, kind: SpanKind | None, title: bool = False) -> str:
        return speakable(text)
