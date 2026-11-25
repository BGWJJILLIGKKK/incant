"""Prepare an independent ebook manuscript from shared ingestion."""

from .base import DraftMethod as Base

ORDER = 2
DESCRIPTION = "prepare an editable ebook manuscript"
MODES = ("ebook",)

__all__ = ["Base", "ORDER", "DESCRIPTION", "MODES"]
