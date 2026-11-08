"""Other e-book and document formats, converted to EPUB and read by `epub` (which this inherits from): Kindle
(mobi, azw, azw3), Word (docx), OpenDocument (odt), RTF, FictionBook (fb2), lit, pdb and local HTML files.

Converters are system tools, not Python packages:
    Calibre's ebook-convert   every format above        apt install calibre
    Pandoc                    docx, odt, rtf, fb2, html apt install pandoc
`converter = "auto"` uses Calibre when installed, else Pandoc for the formats it reads. DRM-protected Kindle
books can't be converted. Docker: build with --build-arg CONVERTERS=pandoc (or calibre).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..base import Ingested
from ..bookmark.bookmark import read_bookmarks
from ..epub.epub import EpubIngest

__all__ = ["EbookIngest"]

CALIBRE_ONLY = {".mobi", ".azw", ".azw3", ".lit", ".pdb"}
PANDOC = {".docx", ".odt", ".rtf", ".fb2", ".html", ".htm"}
FORMATS = CALIBRE_ONLY | PANDOC


class EbookSettings(BaseModel):
    converter: Literal["auto", "calibre", "pandoc"] = "auto"
    timeout_s: float = 300


class EbookIngest(EpubIngest):
    LICENSE = 'project code; Calibre / Pandoc (GPL, tools: output not encumbered)'
    OUTPUT_TERMS = 'none'
    Settings = EbookSettings
    settings: EbookSettings

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() in FORMATS and read_bookmarks(p) is None  # not a bookmark export

    def tool(self, suffix: str) -> tuple[str, str]:
        """(converter name, executable) for this format, or a clear error."""
        want = self.settings.converter
        calibre, pandoc = shutil.which("ebook-convert"), shutil.which("pandoc")
        if want in ("auto", "calibre") and calibre:
            return "calibre", calibre
        if want in ("auto", "pandoc") and pandoc and suffix in PANDOC:
            return "pandoc", pandoc
        need = "Calibre (apt install calibre)" if suffix in CALIBRE_ONLY or want == "calibre" else \
            "Calibre or Pandoc (apt install calibre, or apt install pandoc)"
        raise RuntimeError(f"reading {suffix} files needs {need}; in Docker build with --build-arg CONVERTERS=…")

    def load(self, source: str) -> Ingested:
        src = Path(source)
        name, exe = self.tool(src.suffix.lower())
        with tempfile.TemporaryDirectory(prefix="incant-ebook-") as tmp:
            out = Path(tmp) / f"{src.stem}.epub"
            cmd = [exe, str(src), str(out)] if name == "calibre" else [exe, str(src), "-o", str(out)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.settings.timeout_s)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"{name} took longer than {self.settings.timeout_s:.0f} s converting {src.name}") from None
            if r.returncode != 0 or not out.exists():
                msg = (r.stderr or r.stdout).strip().splitlines()
                hint = " (DRM-protected books can't be converted)" if "DRM" in (r.stderr + r.stdout) else ""
                raise RuntimeError(f"{name} could not convert {src.name}{hint}: {msg[-1] if msg else 'no output'}")
            ing = super().load(str(out))
        ing.warnings.append(f"converted from {src.suffix.lower()} with {name}")
        if ing.title == out.stem:
            ing.title = src.stem.replace("_", " ").replace("-", " ").title()
        return ing

    def check(self) -> list[tuple[bool, str]]:
        found = [n for n, e in (("calibre", "ebook-convert"), ("pandoc", "pandoc")) if shutil.which(e)]
        return [(bool(found), "converters: " + (", ".join(found) if found else
                                                "none (apt install calibre or pandoc)"))]
