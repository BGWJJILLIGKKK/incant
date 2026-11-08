"""English rules: Roman numerals, numbers/years/money/times, abbreviations, ALL CAPS, symbols, archaic hyphenation."""

from __future__ import annotations

from ....schemas import SpanKind
from ....speech import normalize
from ..base import NormalizeMethod

__all__ = ["RulesNormalize"]


class RulesNormalize(NormalizeMethod):
    LICENSE = 'project code'
    OUTPUT_TERMS = 'none'
    def say(self, text: str, kind: SpanKind | None, title: bool = False) -> str:
        return normalize(text, title=title, whole_span=kind is SpanKind.narration)
