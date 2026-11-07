"""Stage 2 — parse: text.md -> chapters.json + chapters/<id>.txt, and optionally characters.json.

Shared helper: chapters.py (deterministic splitting; LLM methods can start from it).
"""

from .base import ParseMethod as Base

DEFAULT = "basic"  # when neither the command line nor [pipeline] names a method
ORDER = 2
DESCRIPTION = "split into chapters; build character profiles"

__all__ = ["Base", "DEFAULT", "ORDER", "DESCRIPTION"]
