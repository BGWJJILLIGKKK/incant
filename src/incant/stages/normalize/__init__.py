"""Stage 4 — normalize: spans -> speech/<chapter>.json, adding TTS-ready `say` text to every span.

Spells out what a TTS engine is likely to misread (numerals, numbers, money, abbreviations, ALL CAPS,
symbols, archaic hyphenation) and applies the pronunciation lexicon. Book text is never changed.
"""

from .base import NormalizeMethod as Base

ORDER = 4
DESCRIPTION = "spell out numbers, abbreviations, numerals, etc. for the TTS; apply the lexicon"

__all__ = ["Base", "ORDER", "DESCRIPTION"]
