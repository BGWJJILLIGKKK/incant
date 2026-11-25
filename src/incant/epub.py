from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from .normalize import SCENE_BREAK, unescape_prose
from .ebook import EbookDraft
from .workdir import atomic_write

XHTML = "http://www.w3.org/1999/xhtml"
EPUB = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
INLINE = re.compile(r"(?<!\\)\*([^*\n]+)\*")
CONTAINER = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def put(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, attrs)
    if text is not None:
        node.text = INVALID_XML.sub("", text)
    return node


def xml(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def xhtml(title, language):
    root = ET.Element("html", {"xmlns": XHTML, "xmlns:epub": EPUB, XML_LANG: language, "lang": language})
    head = put(root, "head")
    put(head, "title", title)
    put(head, "link", rel="stylesheet", href="book.css", type="text/css")
    return root, put(root, "body")


def prose(body, text):
    if text == SCENE_BREAK:
        put(body, "hr")
        return
    p = put(body, "p")
    pieces = INLINE.split(INVALID_XML.sub("", text))
    p.text = unescape_prose(pieces[0])
    for i in range(1, len(pieces), 2):
        emphasis = put(p, "em", unescape_prose(pieces[i]))
        emphasis.tail = unescape_prose(pieces[i + 1])


def export(book: EbookDraft, output: Path, *, source: str, cover: Path | None = None) -> Path:
    chapters = [c for c in book.chapters if any(p.strip() and p != SCENE_BREAK for p in c.paragraphs)]
    if not chapters:
        raise ValueError("Add some text before exporting the book.")
    output = output.expanduser()
    if output.suffix.lower() != ".epub":
        raise ValueError("Output filename must end in .epub")
    package = ET.Element("package", {"xmlns": OPF, "xmlns:dc": DC, "version": "3.0", "unique-identifier": "id"})
    meta = put(package, "metadata")
    put(meta, "dc:identifier", uuid5(NAMESPACE_URL, source).urn, id="id")
    put(meta, "dc:title", book.title)
    put(meta, "dc:language", book.language)
    if book.author:
        put(meta, "dc:creator", book.author)
    if book.description:
        put(meta, "dc:description", book.description)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    put(meta, "meta", stamp, property="dcterms:modified")
    manifest = put(package, "manifest")
    put(manifest, "item", id="nav", href="nav.xhtml", properties="nav", **{"media-type": "application/xhtml+xml"})
    put(manifest, "item", id="css", href="book.css", **{"media-type": "text/css"})
    cover_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}
    cover_ext = cover.suffix.lower() if cover else ""
    cover_href = "cover" + cover_ext
    if cover_ext in cover_type:
        put(manifest, "item", id="cover-image", href=cover_href, properties="cover-image",
            **{"media-type": cover_type[cover_ext]})
    spine = put(package, "spine")
    nav, body = xhtml("Contents", book.language)
    toc = put(body, "nav", **{"epub:type": "toc"})
    put(toc, "h1", "Contents")
    entries = put(toc, "ol")
    data = BytesIO()
    with ZipFile(data, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("EPUB/book.css", "body { line-height: 1.5; } hr { width: 25%; margin: 1.5em auto; }\n")
        if cover and cover_ext in cover_type:
            archive.write(cover, "EPUB/" + cover_href)
        for index, chapter in enumerate(chapters, 1):
            key = f"chapter-{index}"
            href = f"{key}.xhtml"
            put(manifest, "item", id=key, href=href, **{"media-type": "application/xhtml+xml"})
            put(spine, "itemref", idref=key)
            put(put(entries, "li"), "a", chapter.title, href=href)
            page, body = xhtml(chapter.title, book.language)
            put(body, "h1", chapter.title)
            for paragraph in chapter.paragraphs:
                prose(body, paragraph)
            archive.writestr("EPUB/" + href, xml(page))
        archive.writestr("EPUB/nav.xhtml", xml(nav))
        archive.writestr("EPUB/package.opf", xml(package))
    atomic_write(output, data.getvalue())
    return output

