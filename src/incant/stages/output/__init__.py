"""Stage 6 — output: chapter audio + manifest + metadata -> the deliverable (one lossy encode, here)."""

from .base import OutputMethod as Base

DEFAULT = "m4b"  # when neither the command line nor [pipeline] names a method
ORDER = 6
DESCRIPTION = "package audio into the final file(s)"

__all__ = ["Base", "DEFAULT", "ORDER", "DESCRIPTION"]
