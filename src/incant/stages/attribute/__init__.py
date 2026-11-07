"""Stage 3 — attribute: chapters -> spans/<id>.jsonl (narration/dialogue spans with speaker + emotion).

Shared helper: spans.py (deterministic span splitting — the LLM never writes text, it only labels span ids).
"""

from .base import AttributeMethod as Base

DEFAULT = "narrator"  # when neither the command line nor [pipeline] names a method
ORDER = 3
DESCRIPTION = "split into speaker spans; assign character + emotion"

__all__ = ["Base", "DEFAULT", "ORDER", "DESCRIPTION"]
