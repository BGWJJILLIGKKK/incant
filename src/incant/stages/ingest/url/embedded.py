"""Text that JavaScript-built pages ship as data instead of markup.

Next.js (__NEXT_DATA__), Nuxt, Remix/React Router loader streams, Apollo/Redux initial state, JSON-LD
`articleBody`: the article is in the HTML, but inside <script> as JSON, often JSON-encoded twice, often as
an HTML fragment. trafilatura only reads markup, so it sees an empty shell.

This walks every JSON value in every <script>, decodes nested JSON strings, and returns the longest
string that reads as prose. Pages that fetch their text in a separate request after loading (no text in the
HTML at all) aren't covered: that needs a browser to run the page's JavaScript.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

_JSON_STRING = re.compile(r'"(?:[^"\\\n]|\\.){200,}"')  # string literals long enough to hold text
_HTML_FRAGMENT = re.compile(r"</?(?:p|br|div|span|em|i|b|strong|blockquote|h\d)\b[^>]*>", re.I)
_SENTENCE_END = re.compile(r"[.!?…][\"”’')]?\s")
MAX_DEPTH = 3


def _walk(node: object, depth: int) -> Iterator[str]:
    if isinstance(node, str):
        s = node.strip()
        if depth < MAX_DEPTH and s[:1] in "[{" and len(s) > 200:
            try:
                yield from _walk(json.loads(s), depth + 1)  # JSON inside a JSON string (React Router streams)
                return
            except ValueError:
                pass
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk(v, depth)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, depth)


def _script_strings(script: str) -> Iterator[str]:
    body = script.strip()
    if body[:1] in "[{":  # a whole-JSON script: application/json, ld+json, __NEXT_DATA__
        try:
            yield from _walk(json.loads(body), 0)
            return
        except ValueError:
            pass
    for m in _JSON_STRING.finditer(script):  # JSON/JS string literals inside code
        try:
            s = json.loads(m.group(0))
        except ValueError:
            continue
        yield from _walk(s, 0)


def to_text(s: str) -> str:
    """Plain text with blank lines between paragraphs (HTML fragments converted)."""
    if _HTML_FRAGMENT.search(s):
        s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
        s = re.sub(r"</(?:p|div|blockquote|h\d)>", "\n\n", s, flags=re.I)
        s = BeautifulSoup(s, "lxml").get_text()
    return re.sub(r"\n{3,}", "\n\n", s.replace("\r", "")).strip()


def is_prose(text: str, min_words: int) -> bool:
    words = text.split()
    if len(words) < min_words:
        return False
    if sum("://" in w or w.startswith(("{", "<", "data:")) for w in words) > len(words) * 0.02:
        return False  # URLs, markup, code
    mean = sum(map(len, words)) / len(words)
    if not 3.0 <= mean <= 9.0:  # base64 / identifiers / tokens
        return False
    return len(_SENTENCE_END.findall(text)) >= len(words) / 60  # sentences, not a list of tags


def embedded_text(html: str, min_words: int) -> str | None:
    """The longest prose string embedded in the page's scripts, or None."""
    soup = BeautifulSoup(html, "lxml")
    best: tuple[int, str] | None = None
    for tag in soup.find_all("script"):
        for s in _script_strings(tag.string or tag.get_text() or ""):
            if len(s) < 200:
                continue
            t = to_text(s)
            n = len(t.split())
            if (best is None or n > best[0]) and is_prose(t, min_words):
                best = (n, t)
    return best[1] if best else None
