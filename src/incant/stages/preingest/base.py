from __future__ import annotations

from dataclasses import dataclass

from ..base import Context, Method


@dataclass
class SourceItem:
    """One source produced by expanding a container (a bookmark file, a reading list)."""

    source: str  # a file path or URL that an ingest method accepts
    title: str | None = None  # the container's name for it; used when the source has no title of its own


class PreIngestMethod(Method):
    """Decides how ingestion is handled: expand a container into its sources, or leave the source as it is."""

    @classmethod
    def accepts(cls, source: str) -> bool:
        """True if this method claims `source`; drives auto-detection."""
        return False

    @classmethod
    def expand(cls, source: str) -> list[SourceItem] | None:
        """The sources inside `source` (each runs as its own book, as in a batch), or None to ingest it as is."""
        return None

    def run(self, ctx: Context) -> object:
        raise RuntimeError(f"{self.stage}.{self.name} decides how ingestion is handled; it runs on the input "
                           "before the pipeline, not as a stage of a book")
