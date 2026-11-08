"""incant: Ingest Novels, Cast Actors, Narrate Tales. Books in, multi-voice audiobooks out."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("incant")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
