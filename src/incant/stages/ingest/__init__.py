"""Stage 1 — ingest: any source -> work/<slug>/{meta.json, text.md, cover.*}.

Methods implement `accepts(source)` (used for auto-detection) and `load(source) -> Ingested`.
Sources that hold several books (bookmark files) are expanded into items by the preingest stage before
this one runs; ingest always sees one source.
text.md conventions are documented in incant/normalize.py.
"""

from .base import IngestMethod as Base
from .base import Ingested

ORDER = 1
DESCRIPTION = "source (file/URL) -> normalized text + metadata"

__all__ = ["Base", "Ingested", "ORDER", "DESCRIPTION"]

MODES = ("audiobook", "ebook")
