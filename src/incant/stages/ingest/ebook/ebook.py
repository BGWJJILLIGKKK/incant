"""E-book and document formats. Base class for `epub` and `mobi`: converts non-EPUB formats (Word,
ODT, RTF, FictionBook, lit, pdb, local HTML, and fallback Kindle) to EPUB using Calibre or Pandoc.

Converters are system tools, not Python packages:
    Calibre's ebook-convert   every format above        apt install calibre
    Pandoc                    docx, odt, rtf, fb2, html apt install pandoc
`converter = "auto"` uses Calibre when installed, else Pandoc for the formats it reads. DRM-protected Kindle
books can't be converted. Docker: build with --build-arg CONVERTERS=pandoc (or calibre).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ...preingest.bookmark.bookmark import read_bookmarks
from ..base import Ingested, IngestMethod

__all__ = ["EbookIngest"]

CALIBRE_ONLY = {".mobi", ".azw", ".azw3", ".lit", ".pdb"}
PANDOC = {".docx", ".odt", ".rtf", ".fb2", ".html", ".htm"}
FORMATS = CALIBRE_ONLY | PANDOC


# Metadata Pandoc's EPUB writer may act on beyond these (cover-image, css, stylesheet, epub-fonts…) can name
# files or URLs, which the writer would fetch into the book: an untrusted document keeps only these.
_SAFE_META = {"title", "subtitle", "author", "creator", "date", "lang", "language", "description", "publisher",
              "rights", "identifier", "subject"}


def _strip_resources(node):
    """A Pandoc JSON tree with nothing left that names a file or URL to fetch: images become their alt text,
    raw passthrough (HTML, LaTeX…) is dropped. The writer runs outside --sandbox (it needs Pandoc's data files),
    so this is what keeps a document from pulling local files or URLs into the EPUB."""
    if isinstance(node, list):
        return [_strip_resources(n) for n in node]
    if not isinstance(node, dict):
        return node
    kind = node.get("t")
    if kind == "Image":  # [attr, alt inlines, target]
        return {"t": "Span", "c": [["", [], []], _strip_resources(node["c"][1])]}
    if kind == "RawBlock":
        return {"t": "Div", "c": [["", [], []], []]}
    if kind == "RawInline":
        return {"t": "Str", "c": ""}
    return {k: _strip_resources(v) for k, v in node.items()}


def pandoc_to_epub(exe: str, src: Path, out: Path, timeout: float) -> subprocess.CompletedProcess:
    """Read under --sandbox (only `src` is opened), strip fetchable resources, then write the EPUB."""
    r = subprocess.run([exe, str(src), "-t", "json", "--sandbox"], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return r
    doc = json.loads(r.stdout)
    doc["meta"] = {k: _strip_resources(v) for k, v in doc.get("meta", {}).items() if k in _SAFE_META}
    doc["blocks"] = _strip_resources(doc.get("blocks", []))
    clean = out.with_suffix(".json")
    clean.write_text(json.dumps(doc))
    return subprocess.run([exe, str(clean), "-f", "json", "-o", str(out)], capture_output=True, text=True,
                          timeout=timeout)


class EbookSettings(BaseModel):
    converter: Literal["auto", "calibre", "pandoc"] = "auto"
    timeout_s: float = 300


class EbookIngest(IngestMethod):
    LICENSE = 'project code; Calibre / Pandoc (GPL, tools: output not encumbered)'
    OUTPUT_TERMS = 'none'
    Settings = EbookSettings
    settings: EbookSettings

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() in FORMATS and read_bookmarks(p) is None  # not a bookmark export

    def load_epub(self, path: Path | str) -> Ingested:
        from ..epub.epub import ingest_epub

        return ingest_epub(Path(path))

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
            try:
                if name == "calibre":
                    r = subprocess.run([exe, str(src), str(out)], capture_output=True, text=True,
                                       timeout=self.settings.timeout_s)
                else:  # untrusted documents can't make Pandoc read other local files or fetch URLs
                    r = pandoc_to_epub(exe, src, out, self.settings.timeout_s)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"{name} took longer than {self.settings.timeout_s:.0f} s converting {src.name}") from None
            if r.returncode != 0 or not out.exists():
                msg = (r.stderr or r.stdout).strip().splitlines()
                hint = " (DRM-protected books can't be converted)" if "DRM" in (r.stderr + r.stdout) else ""
                raise RuntimeError(f"{name} could not convert {src.name}{hint}: {msg[-1] if msg else 'no output'}")
            ing = self.load_epub(out)
        ing.input_format = src.suffix.lower().lstrip(".")
        ing.warnings.append(f"converted from {src.suffix.lower()} with {name}")
        if ing.title == out.stem:
            ing.title = src.stem.replace("_", " ").replace("-", " ").title()
        return ing

    def check(self) -> list[tuple[bool, str]]:
        found = [n for n, e in (("calibre", "ebook-convert"), ("pandoc", "pandoc")) if shutil.which(e)]
        return [(bool(found), "converters: " + (", ".join(found) if found else
                                                "none (apt install calibre or pandoc)"))]
