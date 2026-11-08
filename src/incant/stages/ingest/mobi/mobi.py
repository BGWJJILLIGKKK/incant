"""Kindle books (mobi, azw, azw3, prc, kf8) without Calibre — child of `ebook` (and so of `epub`).

The `mobi` package (KindleUnpack, GPL-3.0; a tool — it puts no terms on the audiobook) unpacks the file:
  KF8 / AZW3   -> an EPUB, read by `epub`
  MOBI 6       -> one HTML file in Kindle's old markup: chapter titles are large-font paragraphs, chapters are
                  separated by <mbp:pagebreak>, the table of contents is a table. Read here; the book's
                  content.opf gives title, author and cover.
Project Gutenberg boilerplate is removed in both cases. DRM-protected books can't be read.
Without the `mobi` package (extra: mobi), `ebook` converts mobi/azw/azw3 with Calibre instead (prc/kf8 need
this method).
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

from ....normalize import SCENE_BREAK, clean_paragraph, clean_unicode, heading, paragraphs_from_plain
from ..base import Ingested
from ..ebook.ebook import EbookIngest
from ..gutenberg.gutenberg import strip_gutenberg

__all__ = ["MobiIngest"]

FORMATS = {".mobi", ".azw", ".azw3", ".prc", ".kf8"}
HEADING_FONT = 5  # <font size="5"> and up, on a short line, is a heading
_DC = re.compile(r"<dc:(title|creator)[^>]*>(.*?)</dc:\1>", re.S | re.I)


def mobi6_paragraphs(html: str) -> list[str]:
    """Kindle's old HTML -> paragraphs with '# ' headings."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(["table", "guide", "head"]):  # table of contents, navigation
        t.decompose()
    out: list[str] = []
    for el in soup.find_all(["p", "div", "blockquote", "li", "h1", "h2", "h3", "h4", "mbp:pagebreak"]):
        if el.name == "mbp:pagebreak":
            if out and out[-1] != SCENE_BREAK:
                out.append(SCENE_BREAK)  # becomes a chapter boundary only if a heading follows
            continue
        if el.find(["p", "div", "blockquote", "li"]):
            continue  # a container: its children are visited themselves
        text = clean_unicode(el.get_text(" ", strip=True))
        if not text:
            continue
        sizes = [int(f["size"]) for f in el.find_all("font") if str(f.get("size", "")).lstrip("+").isdigit()]
        big = el.name in ("h1", "h2", "h3", "h4") or (sizes and max(sizes) >= HEADING_FONT)
        if big and len(text) <= 80:
            out.append(heading(text))
        else:
            out.append(clean_paragraph(text))
    # Gutenberg's licence header and footer, as for plain-text books
    joined = "\n\n".join(out)
    if "PROJECT GUTENBERG" in joined.upper():
        joined = strip_gutenberg(re.sub(r"^\*\*\*", "\n***", joined, flags=re.M))
        out = [p for p in joined.split("\n\n") if p.strip()]
    out = [p for p in out if not re.fullmatch(r"#\s*(?:table of )?contents\.?", p, re.I)]
    while out and out[0] == SCENE_BREAK:
        out.pop(0)
    return out


class MobiIngest(EbookIngest):
    LICENSE = "project code; mobi / KindleUnpack (GPL-3.0, a tool: output not encumbered)"
    OUTPUT_TERMS = "none"

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() in FORMATS and importlib.util.find_spec("mobi") is not None

    def load(self, source: str) -> Ingested:
        import mobi

        src = Path(source)
        try:
            tmp, out = mobi.extract(str(src))
        except Exception as e:  # noqa: BLE001 — the unpacker raises plain exceptions for DRM and damage
            hint = " (DRM-protected Kindle books can't be read)" if "drm" in str(e).lower() or "encrypt" in str(e).lower() else ""
            raise RuntimeError(f"could not unpack {src.name}{hint}: {e}") from None
        try:
            out = Path(out)
            if out.suffix.lower() == ".epub":
                ing = super(EbookIngest, self).load(str(out))  # straight to epub, no converter needed
                ing.warnings.append("unpacked from KF8 with mobi")
            else:
                ing = self._mobi6(out, src)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not ing.paragraphs:
            raise RuntimeError(f"no text found in {src.name}")
        return ing

    def _mobi6(self, html: Path, src: Path) -> Ingested:
        folder = html.parent
        paragraphs = mobi6_paragraphs(html.read_text(encoding="utf-8", errors="replace"))
        opf = next(folder.glob("*.opf"), None)
        meta = dict((k.lower(), clean_unicode(re.sub(r"<[^>]+>", "", v)).strip()) for k, v in
                    _DC.findall(opf.read_text(errors="replace"))) if opf else {}
        cover = None
        images = sorted(folder.parent.rglob("cover*.jp*g")) + sorted(folder.parent.rglob("cover*.png"))
        if images:
            cover = (images[0].suffix.lstrip(".").replace("jpeg", "jpg"), images[0].read_bytes())
        if not any(p.startswith("# ") for p in paragraphs):
            paragraphs = paragraphs_from_plain("\n\n".join(paragraphs))  # chapters then come from text patterns
        return Ingested(title=meta.get("title") or src.stem.replace("_", " ").title(), author=meta.get("creator"),
                        paragraphs=paragraphs, source_format="epub", cover=cover,
                        warnings=["unpacked from MOBI 6 with mobi"])
