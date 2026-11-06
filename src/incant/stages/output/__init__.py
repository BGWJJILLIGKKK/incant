"""Stage 5 — output: chapter audio + manifest + metadata -> the deliverable (one lossy encode, here)."""

from .base import OutputMethod as Base

ORDER = 5
DESCRIPTION = "package audio into the final file(s)"

__all__ = ["Base", "ORDER", "DESCRIPTION"]
