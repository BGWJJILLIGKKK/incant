"""Read an EPUB 2/3 container: metadata, manifest, spine, table of contents and cover. Standard library zipfile
plus lxml; no HTML handling here (epub.py does that).

    book = EpubFile.open(path)
    book.title, book.creator, book.language, book.description
    for doc in book.documents():          # spine order, navigation document skipped
        doc.href, doc.content             # href relative to the package (OPF) directory
    book.toc                              # {href without fragment: first TOC title}
    book.cover()                          # ("jpg" | "png", bytes) or None
"""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

__all__ = ["EpubError", "EpubFile", "Item"]

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "x": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}
DOCUMENT_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbook+xml"}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=True)


class EpubError(ValueError):
    pass


@dataclass
class Item:
    id: str
    href: str  # relative to the package directory, unquoted
    media_type: str
    properties: set[str] = field(default_factory=set)
    _zip_path: str = ""
    _zip: zipfile.ZipFile | None = None

    @property
    def content(self) -> bytes:
        assert self._zip is not None
        return self._zip.read(self._zip_path)


def _xml(data: bytes):
    root = etree.fromstring(data, _PARSER)
    if root is None:
        raise EpubError("unreadable XML")
    return root


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


class EpubFile:
    def __init__(self, zf: zipfile.ZipFile, opf_path: str):
        self._zip = zf
        self._names = set(zf.namelist())
        self.base = posixpath.dirname(opf_path)
        opf = _xml(zf.read(opf_path))
        self._meta = opf.find("opf:metadata", NS)
        if self._meta is None:
            self._meta = opf.find(".//{*}metadata")
        self.items: dict[str, Item] = {}
        for el in opf.iterfind("opf:manifest/opf:item", NS):
            iid, href = el.get("id"), el.get("href")
            if not iid or not href:
                continue
            href = unquote(href.split("#")[0])
            zp = posixpath.normpath(posixpath.join(self.base, href))
            self.items[iid] = Item(iid, href, (el.get("media-type") or "").strip().lower(),
                                   set((el.get("properties") or "").split()), zp, zf)
        spine = opf.find("opf:spine", NS)
        self.spine: list[str] = [el.get("idref") for el in spine.iterfind("opf:itemref", NS)] if spine is not None else []
        self._ncx_id = spine.get("toc") if spine is not None else None
        self._cover_meta = next((el.get("content") for el in self._meta.iterfind("opf:meta", NS)
                                 if el.get("name") == "cover"), None) if self._meta is not None else None
        self.toc = self._read_toc()

    @classmethod
    def open(cls, path: Path) -> EpubFile:
        try:
            zf = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise EpubError(f"{path.name}: not an EPUB (not a zip file)") from e
        try:
            root = _xml(zf.read("META-INF/container.xml"))
            opf = root.find(".//c:rootfile", NS)
            if opf is None or not opf.get("full-path"):
                raise EpubError("no rootfile in META-INF/container.xml")
            return cls(zf, unquote(opf.get("full-path")))
        except KeyError as e:
            raise EpubError(f"{path.name}: missing {e}") from e

    # ------------------------------------------------------------ metadata

    def dc(self, name: str) -> str | None:
        if self._meta is None:
            return None
        el = self._meta.find(f"dc:{name}", NS)
        v = _text(el)
        return v or None

    @property
    def title(self) -> str | None:
        return self.dc("title")

    @property
    def creator(self) -> str | None:
        return self.dc("creator")

    @property
    def language(self) -> str | None:
        return self.dc("language")

    @property
    def description(self) -> str | None:
        return self.dc("description")

    # ------------------------------------------------------------ content

    def _exists(self, item: Item) -> bool:
        return item._zip_path in self._names

    def documents(self) -> list[Item]:
        out = []
        for idref in self.spine:
            it = self.items.get(idref)
            if it is None or it.media_type not in DOCUMENT_TYPES or "nav" in it.properties or not self._exists(it):
                continue
            out.append(it)
        return out

    def cover(self) -> tuple[str, bytes] | None:
        items = list(self.items.values())
        candidates = [i for i in items if "cover-image" in i.properties]
        if self._cover_meta and self._cover_meta in self.items:
            candidates.append(self.items[self._cover_meta])
        candidates += [i for i in items if i.media_type.startswith("image/") and "cover" in i.href.lower()]
        for it in candidates:
            ext = posixpath.splitext(it.href)[1].lower().lstrip(".")
            if ext in ("jpg", "jpeg", "png") and self._exists(it):
                return ("jpg" if ext == "jpeg" else ext, it.content)
        return None

    # ------------------------------------------------------------ table of contents

    def _relative(self, toc_item: Item, href: str) -> str:
        """A TOC link (relative to the TOC file) as an href relative to the package directory."""
        target = posixpath.normpath(posixpath.join(posixpath.dirname(toc_item._zip_path), unquote(href.split("#")[0])))
        return posixpath.relpath(target, self.base) if self.base else target

    def _read_toc(self) -> dict[str, str]:
        out: dict[str, str] = {}
        ncx = self.items.get(self._ncx_id or "") or next(
            (i for i in self.items.values() if i.media_type == "application/x-dtbncx+xml"), None)
        nav = next((i for i in self.items.values() if "nav" in i.properties), None)
        try:
            if ncx is not None and self._exists(ncx):
                for point in _xml(ncx.content).iter("{%s}navPoint" % NS["ncx"]):
                    label = _text(point.find("ncx:navLabel/ncx:text", NS))
                    content = point.find("ncx:content", NS)
                    if content is not None and content.get("src"):
                        out.setdefault(self._relative(ncx, content.get("src")), label)
            elif nav is not None and self._exists(nav):
                root = _xml(nav.content)
                navs = [n for n in root.iter("{%s}nav" % NS["x"])]
                toc = next((n for n in navs if n.get("{%s}type" % NS["epub"]) == "toc"), navs[0] if navs else None)
                for a in (toc.iter("{%s}a" % NS["x"]) if toc is not None else []):
                    if a.get("href"):
                        out.setdefault(self._relative(nav, a.get("href")), _text(a))
        except EpubError:
            pass  # a broken TOC only costs chapter titles
        return out
