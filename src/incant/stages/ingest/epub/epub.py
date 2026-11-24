"""EPUB ingest. Uses the spine for reading order and TOC/headings for chapter boundaries."""

from __future__ import annotations

import re
import warnings
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

from ....normalize import SCENE_BREAK, clean_paragraph, clean_unicode, heading, is_scene_break
from ..base import Ingested
from ..ebook.ebook import EbookIngest
from .container import EpubFile

__all__ = ["EpubIngest"]

_BLOCKS = {"p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "dd", "dt", "td", "figcaption"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3}
_DROP = {"script", "style", "sup", "svg", "figure", "nav", "aside", "table"}
# Gutenberg/ebookmaker boilerplate and non-narrative furniture, matched on id or class.
_DROP_ID = re.compile(r"^pg-(header|footer)$")
_DROP_CLASS = re.compile(r"caption|figcenter|figleft|figright|pageno|pagenum|footnote|pg-boilerplate", re.I)


def _text_of(el: Tag) -> str:
    for t in el.find_all(["i", "em"]):
        if t.get_text(strip=True):
            t.insert_before("*")
            t.insert_after("*")
            t.unwrap()
    for br in el.find_all("br"):
        br.replace_with(" ")
    return clean_paragraph(el.get_text())


def _is_leaf_block(el: Tag) -> bool:
    return el.name in _BLOCKS and not any(isinstance(c, Tag) and c.name in _BLOCKS for c in el.descendants)


def _is_link_list(el: Tag) -> bool:
    """A block that is (almost) entirely internal links: a table of contents entry."""
    text = el.get_text(" ", strip=True)
    links = " ".join(a.get_text(" ", strip=True) for a in el.find_all("a", href=True))
    return bool(text) and len(links) >= 0.8 * len(text)


Block = tuple[str, int | None, str]  # (kind "h"/"p", heading level, text)


def _doc_blocks(html: bytes) -> list[Block]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    body = soup.body or soup
    for t in body.find_all(_DROP):
        t.decompose()
    for t in body.find_all(True):
        if t.decomposed:
            continue
        if _DROP_ID.match(t.get("id") or "") or _DROP_CLASS.search(" ".join(t.get("class") or [])):
            t.decompose()
        elif t.get("epub:type") in ("noteref", "footnote", "endnote", "pagebreak"):
            t.decompose()
    for img in body.find_all("img"):  # keep image drop-caps ("M" + "R. BENNET"), drop other images
        alt = (img.get("alt") or "").strip()
        img.replace_with(alt if 0 < len(alt) <= 2 else "")

    blocks: list[Block] = []
    for el in body.find_all(True):
        if not _is_leaf_block(el) or _is_link_list(el):
            continue
        text = _text_of(el)
        if text:
            blocks.append(("h" if el.name in _HEADINGS else "p", _HEADINGS.get(el.name), text))
    if not blocks:  # bare text directly in <body>
        loose = " ".join(s.strip() for s in body.find_all(string=True, recursive=False) if isinstance(s, NavigableString))
        if loose.strip():
            blocks.append(("p", None, clean_paragraph(loose)))
    return blocks


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _assemble(docs: list[tuple[list[Block], str | None]], book_title: str) -> list[str]:
    """Pick the chapter heading level book-wide (the most frequent of h1-h3), then emit text.md paragraphs.

    Headings above the chapter level (parts) also become boundaries; headings below it are kept
    as plain text. A heading that just repeats the book title is not a boundary.
    """
    title_key = _norm(book_title)
    counts = Counter(lvl for blocks, _ in docs for kind, lvl, t in blocks if kind == "h" and _norm(t) != title_key)
    chapter_level = max(counts, key=lambda lvl: (counts[lvl], -lvl)) if counts else None

    out: list[str] = []
    for blocks, toc_title in docs:
        if not any(k == "h" and lvl <= (chapter_level or 0) for k, lvl, _ in blocks) and toc_title and blocks:
            if _norm(toc_title) != title_key:
                out.append(heading(toc_title))
        for kind, lvl, text in blocks:
            if kind == "h" and chapter_level and lvl <= chapter_level:
                t = text.strip("* ")
                if _norm(t) == title_key:
                    continue
                out.append(heading(t))
            elif is_scene_break(text):
                out.append(SCENE_BREAK)
            else:
                out.append(text)
    return out


def ingest_epub(path: Path) -> Ingested:
    book = EpubFile.open(path)
    title = book.title or path.stem
    docs: list[tuple[list[Block], str | None]] = []
    for item in book.documents():
        html = clean_unicode(item.content.decode("utf-8", errors="replace")).encode()
        docs.append((_doc_blocks(html), book.toc.get(item.href)))
    paragraphs = _assemble(docs, title)

    lang = (book.language or "en").split("-")[0]
    return Ingested(
        title=title,
        author=book.creator,
        language=lang,
        description=book.description,
        paragraphs=paragraphs,
        source_format="epub",
        cover=book.cover(),
    )


class EpubIngest(EbookIngest):
    """EPUB 2/3: spine order, book-wide chapter heading level, TOC titles, cover, Gutenberg cleanup."""
    LICENSE = 'project code (BSD-3-Clause)'
    OUTPUT_TERMS = 'none'

    @classmethod
    def accepts(cls, source: str) -> bool:
        return Path(source).is_file() and Path(source).suffix.lower() == ".epub"

    def load(self, source: str) -> Ingested:
        return ingest_epub(Path(source))

    def check(self) -> list[tuple[bool, str]]:
        return []
