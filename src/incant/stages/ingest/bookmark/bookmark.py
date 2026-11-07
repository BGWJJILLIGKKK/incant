"""Bookmark files: a container of web pages, each ingested as its own book by the url method.

Formats (recognised by content, not just extension, so an ordinary .html/.json file is never claimed):
  * Netscape bookmark HTML: the export format of Firefox, Chrome, Edge, Safari, Pocket, Pinboard…
    (starts with <!DOCTYPE NETSCAPE-Bookmark-file-1>)
  * Chrome/Edge "Bookmarks" JSON (roots -> children[] with type "url")
  * Firefox bookmark backup JSON (children[] with "uri")
  * A plain JSON list of {"url"|"href"|"uri", "title"|"name"} objects, or {"bookmarks": [...]}

Only http(s) links are kept (no javascript:, place:, file:); duplicates (ignoring #fragments) keep their
first title. Pages behind a login use the url method's per-site credentials ([ingest.url.sites."host"]).
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag

from ..base import Ingested, IngestMethod, SourceItem

__all__ = ["BookmarkIngest"]

NETSCAPE_DOCTYPE = "netscape-bookmark-file"
MAX_JSON_BYTES = 50 * 1024 * 1024
_URL_KEYS = ("url", "uri", "href")
_TITLE_KEYS = ("title", "name")


class _Anchors(HTMLParser):
    """Every <a href> in document order with its text. Netscape exports aren't well-formed HTML
    (<DT> is never closed, attributes vary by browser), so no regexes over the markup."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._close()
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a":
            self._close()

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def _close(self):
        if self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
        self._href = None

    def close(self):
        super().close()
        self._close()


def parse_netscape(html: str) -> list[tuple[str, str]]:
    p = _Anchors()
    p.feed(html)
    p.close()
    return p.links


def parse_json(data: object) -> list[tuple[str, str]]:
    """(url, title) for every bookmark-like object anywhere in the tree, in document order."""
    out: list[tuple[str, str]] = []

    def walk(node: object):
        if isinstance(node, dict):
            url = next((node[k] for k in _URL_KEYS if isinstance(node.get(k), str)), None)
            if url:
                title = next((node[k] for k in _TITLE_KEYS if isinstance(node.get(k), str)), "")
                out.append((url, title.strip()))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def _looks_like_bookmark_json(data: object) -> bool:
    if isinstance(data, dict):
        if "roots" in data and isinstance(data["roots"], dict):  # Chrome / Edge
            return True
        if str(data.get("type", "")).startswith("text/x-moz-place"):  # Firefox backup
            return True
        if isinstance(data.get("bookmarks"), list):
            return True
        return False
    if isinstance(data, list) and data:  # [{url, title}, …]
        return all(isinstance(x, dict) and any(isinstance(x.get(k), str) for k in _URL_KEYS) for x in data)
    return False


def _read_json(p: Path) -> object | None:
    if p.stat().st_size > MAX_JSON_BYTES:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None


def read_bookmarks(path: Path) -> list[tuple[str, str]] | None:
    """Raw (url, title) pairs, or None if the file isn't a bookmark file."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
        if NETSCAPE_DOCTYPE not in head.casefold():
            return None
        return parse_netscape(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".json":
        data = _read_json(path)
        return parse_json(data) if data is not None and _looks_like_bookmark_json(data) else None
    return None


def unique_pages(links: list[tuple[str, str]]) -> list[SourceItem]:
    seen: dict[str, SourceItem] = {}
    for url, title in links:
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        key = urldefrag(url).url.rstrip("/")
        if key not in seen:
            seen[key] = SourceItem(source=url, title=title or None)
    return list(seen.values())


class BookmarkIngest(IngestMethod):
    """A browser bookmark export: every bookmarked page becomes its own book (run as a batch)."""

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and read_bookmarks(p) is not None

    @classmethod
    def expand(cls, source: str) -> list[SourceItem] | None:
        p = Path(source)
        links = read_bookmarks(p) if p.is_file() else None
        return None if links is None else unique_pages(links)

    def load(self, source: str) -> Ingested:
        raise RuntimeError(f"'{source}' is a bookmark file holding many pages; pass it as the input to run each "
                           "page as its own book")
