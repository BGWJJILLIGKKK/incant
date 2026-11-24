"""Stage 0 — preingest (ingest normalization): decide how ingestion is handled.

Methods implement `accepts(source)` (used for auto-detection) and `expand(source) -> [SourceItem] | None`:
a list means the source is a container — each item is ingested as its own book, like a directory batch;
None means ingest the source as given. PRE stages are consulted by the CLI on the raw input, before the
pipeline runs; they are not per-book steps.
"""

from .base import PreIngestMethod as Base
from .base import SourceItem

ORDER = 0
PRE = True
DESCRIPTION = "how the input is ingested: as one book, or expanded into a batch"

__all__ = ["Base", "SourceItem", "ORDER", "PRE", "DESCRIPTION"]

MODES = ("audiobook", "ebook")
