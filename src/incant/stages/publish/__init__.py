"""Package the reviewed ebook manuscript."""

from .base import PublishMethod as Base

ORDER = 3
DESCRIPTION = "export a reflowable EPUB"
MODES = ("ebook",)

__all__ = ["Base", "ORDER", "DESCRIPTION", "MODES"]
