"""Archive of Our Own (AO3) works: a multi-chapter work is ingested as one book, one chapter per part.

Any URL of a work — the work page, one of its chapters, or its path under a collection — is the same
book: chapters are listed on the work's /navigate page and fetched in the site's own order, each becoming
a '# <chapter title>' chapter. Title, author and blurb come from the work page's own header (its page
metadata carries the site's title template). Single-chapter works are just that one page.

Adult-rated works are unlocked by default (view_adult = true): the fetcher gets AO3's view_adult cookie
and the ?view_adult=true parameter, which together pass the site's age confirmation. Works restricted to
logged-in users need your own session: cookies under [ingest.url.sites."archiveofourown.org"] (settings
and credentials are inherited from `url`).
"""

from __future__ import annotations

import re
from http.cookiejar import Cookie
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..base import Ingested
from ..url import url as url_mod
from ..url.url import Fetcher, Page, Part, Series, UrlIngest, UrlSettings

__all__ = ["Ao3Ingest"]

HOSTS = {"archiveofourown.org", "archiveofourown.com", "archiveofourown.net", "archiveofourown.gay", "ao3.org"}
_WORK_PATH = re.compile(r"/works/(\d+)(?:/chapters/\d+)?/?$")


class Ao3Settings(UrlSettings):
    view_adult: bool = True  # pass AO3's age confirmation (cookie + ?view_adult=true)
    max_parts: int = 200  # chapters per work
    part_delay_s: float = 1.0  # pause between chapter fetches


def work_of(url: str) -> tuple[str, str] | None:
    """(origin, canonical work path) — collection prefixes and chapter suffixes collapse to one address."""
    u = urlsplit(url)
    m = _WORK_PATH.search(u.path)
    return (f"https://{u.netloc}", f"/works/{m.group(1)}") if m else None


def work_meta(html: str) -> tuple[str | None, str | None, str | None]:
    """(title, author, blurb) from the work page's own header — its page metadata carries the site's
    "… by … ⋆ AO3" title template instead."""
    soup = BeautifulSoup(html, "lxml")
    h = soup.select_one("h2.title.heading")
    title = " ".join(h.get_text(" ", strip=True).split()) if h else None
    names = list(dict.fromkeys(" ".join(a.get_text(" ", strip=True).split())
                               for a in soup.select('h3.heading a[rel^="author"], div.header a[rel^="author"]')))
    author = ", ".join(n for n in names if n) or None  # AO3 lists several authors, comma-separated
    # the blurb is the blockquote inside div.summary; a bare blockquote.userstuff would catch the author's notes
    blurb = soup.select_one("div.summary blockquote.userstuff, blockquote.userstuff.summary")
    return title, author, (" ".join(blurb.get_text(" ", strip=True).split()) if blurb else None)


def adult_cookie(fetcher: Fetcher, host: str) -> None:
    """AO3's age confirmation is a cookie; the ?view_adult=true parameter only sets it as a side effect
    (and is ignored on the work page itself). domain_specified=False: the cookie also goes to www.*."""
    fetcher.jar.set_cookie(Cookie(0, "view_adult", "true", None, False, host.lower(), False, False, "/", True,
                                  True, None, True, None, None, {}))


class Ao3Ingest(UrlIngest):
    """An AO3 work: all its chapters as one book, in the site's order; adult works unlocked by default."""
    LICENSE = 'project code; trafilatura, Playwright (Apache-2.0)'
    OUTPUT_TERMS = 'none'
    Settings = Ao3Settings
    settings: Ao3Settings

    @classmethod
    def accepts(cls, source: str) -> bool:
        u = urlsplit(source)
        host = (u.hostname or "").lower().removeprefix("www.")
        return u.scheme in ("http", "https") and host in HOSTS and bool(_WORK_PATH.search(u.path))

    def _adult(self, url: str) -> str:
        u = urlsplit(url)
        q = parse_qsl(u.query, keep_blank_values=True)
        if self.settings.view_adult and ("view_adult", "true") not in q:
            q.append(("view_adult", "true"))
        return urlunsplit(u._replace(scheme="https", query=urlencode(q)))

    def load(self, source: str) -> Ingested:
        fetcher = Fetcher(self.settings, base=self.cfg.paths.data_dir)
        host = (urlsplit(source).hostname or "").lower().removeprefix("www.")
        if self.settings.view_adult and host in HOSTS:
            adult_cookie(fetcher, host)
        page = url_mod.fetch_page(self._adult(source), fetcher)
        found = self.series(page, fetcher)
        if found is None or len(found.parts) < 2:
            ing = page.ingested
            work = work_of(page.url)
            if work:  # any URL of a single-chapter work (a chapter link, a collection path) is the same book
                ing.source = work[0] + work[1]
        else:
            ing = self.collect(page, found, fetcher)
        # "Chapter Text" is AO3's section label above the body, not story text
        ing.paragraphs = [p for p in ing.paragraphs if p.strip() != "Chapter Text"]
        title, author, blurb = work_meta(page.html)
        if title:
            ing.title = title
        ing.author = ing.author or author
        ing.description = ing.description or blurb
        return ing

    def series(self, page: Page, fetcher: Fetcher) -> Series | None:
        work = work_of(page.url)
        if not work:
            return None
        origin, path = work
        try:
            _, nav_html, _ = fetcher.get(self._adult(origin + path + "/navigate"))
        except url_mod.FetchError:
            return None
        nav = BeautifulSoup(nav_html, "lxml")
        parts: list[Part] = []
        seen: set[str] = set()
        for a in nav.find_all("a", href=re.compile(rf"{re.escape(path)}/chapters/\d+$")):
            href = a["href"].split("?")[0]
            if href in seen:
                continue
            seen.add(href)
            # the navigate page numbers its links ("2. Chapter Title"): the index is the site's, not the title's
            title = re.sub(r"^\s*\d+\.\s+", "", " ".join(a.get_text(" ", strip=True).split()))
            url = href if href.startswith("http") else origin + href
            parts.append(Part(self._adult(url), title or href.rsplit("/", 1)[-1]))
        if len(parts) < 2:
            return None
        skipped: list[str] = []
        if len(parts) > self.settings.max_parts:
            skipped.append(f"{len(parts) - self.settings.max_parts} chapters beyond max_parts = "
                           f"{self.settings.max_parts}")
            parts = parts[: self.settings.max_parts]
        title, author, _ = work_meta(page.html)
        return Series(url=origin + path, title=title or page.ingested.title, parts=parts, author=author,
                      skipped=skipped)
