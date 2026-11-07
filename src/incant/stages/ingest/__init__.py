"""Stage 1 — ingest: any source -> work/<slug>/{meta.json, text.md, cover.*}.

Methods implement `accepts(source)` (used for auto-detection) and `load(source) -> Ingested`.
Container methods (bookmark files) implement `expand(source) -> [SourceItem]` instead; the CLI runs each
item as its own book, like a directory batch.
text.md conventions are documented in incant/normalize.py.
"""

from .base import IngestMethod as Base
from .base import Ingested, SourceItem

ORDER = 1
DESCRIPTION = "source (file/URL) -> normalized text + metadata"

__all__ = ["Base", "Ingested", "SourceItem", "ORDER", "DESCRIPTION"]
