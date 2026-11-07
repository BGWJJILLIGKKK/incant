"""Single-page URL ingest via trafilatura, with optional per-site credentials.

Credentials (all optional) go in incant.local.toml (git-ignored) or come from environment variables:

    [ingest.url.sites."example.com"]          # this host and its subdomains
    username = "me"
    password_env = "EXAMPLE_PASSWORD"         # or: password = "…"   (HTTP Basic)
    # bearer_env = "EXAMPLE_TOKEN"            # or: bearer = "…"     (Authorization: Bearer)
    # headers = { "X-Api-Key" = "…" }        # any extra request headers
    # cookies = "~/cookies/example.txt"       # cookies.txt (Netscape format) exported from a logged-in browser

Hosts without an entry fall back to ~/.netrc (`netrc = true`). Credentials are attached only to requests
for the site they belong to, including across redirects, and never reach logs, pipeline.json or meta.json.
Multi-page serial crawling is future work.
"""

from __future__ import annotations

import netrc as netrc_mod
import os
import re
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from pydantic import BaseModel, Field

from ....normalize import clean_unicode, paragraphs_from_plain
from ..base import Ingested, IngestMethod
from .embedded import embedded_text

__all__ = ["UrlIngest"]

MAX_REDIRECTS = 10
_PASSWORD_FIELD = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.I)
LOGIN_PAGE_MAX_WORDS = 300  # a page this short with a password box is a login wall, not the article
# Interstitials served instead of the content: age gates, consent walls, bot checks, JavaScript-only pages.
_GATE = re.compile(
    r"\b(?:18 (?:years )?or older|over 18|confirm (?:that )?you are (?:at least )?\d\d|verify your age|age verification"
    r"|adult content|mature content|enable javascript|javascript (?:is )?required|requires javascript"
    r"|log ?in to (?:view|read|continue)|sign ?in to (?:view|read|continue)|members only|subscribe to (?:read|continue)"
    r"|verify you are (?:a )?human|are you a robot|captcha|access denied|checking your browser)\b", re.I)


class Site(BaseModel):
    username: str | None = None
    password: str | None = None
    password_env: str | None = None
    bearer: str | None = None
    bearer_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: str | None = None  # path to a Netscape cookies.txt


class UrlSettings(BaseModel):
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64) incant (personal audiobook use)"
    timeout_s: float = 30.0
    netrc: bool = True  # use ~/.netrc ($NETRC) for hosts without a [sites] entry
    sites: dict[str, Site] = Field(default_factory=dict)
    # Pages with less extracted text than this are refused: almost always a gate or an empty shell, not a story.
    min_words: int = 100
    # JavaScript-built pages often carry the article as JSON inside <script> (Next.js, Nuxt, Remix, JSON-LD).
    # Used when the markup has too little text. Higher bar than min_words: comments and blurbs live there too.
    embedded: bool = True
    embedded_min_words: int = 400


class FetchError(RuntimeError):
    pass


def _env(name: str | None, what: str, host: str) -> str | None:
    if not name:
        return None
    v = os.environ.get(name)
    if v is None:
        raise FetchError(f"{what} for {host}: environment variable {name} is not set")
    return v


class Fetcher:
    """GET with per-site credentials. Credentials go only to their own site: redirects are followed by hand
    and each hop gets the credentials of *its* host (cookies are domain-scoped by the jar itself)."""

    def __init__(self, settings: UrlSettings, transport: httpx.BaseTransport | None = None):
        self.s = settings
        self.jar = MozillaCookieJar()
        for host, site in settings.sites.items():
            if site.cookies:
                p = Path(site.cookies).expanduser()
                try:
                    self.jar.load(str(p), ignore_discard=True, ignore_expires=True)
                except FileNotFoundError:
                    raise FetchError(f"cookies for {host}: {p} does not exist") from None
                except (OSError, LoadError):  # (the exception text can quote cookie lines: not shown)
                    raise FetchError(f"cookies for {host}: {p} is not a Netscape-format cookies.txt "
                                     "(first line '# Netscape HTTP Cookie File', then 7 tab-separated fields per cookie)") from None
        for c in self.jar:  # browser exports write session cookies as expiry 0, which reads as long expired
            if c.expires == 0:
                c.expires, c.discard = None, True
        self.client = httpx.Client(timeout=settings.timeout_s, follow_redirects=False, cookies=self.jar,
                                   headers={"User-Agent": settings.user_agent,
                                            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
                                   transport=transport)
        self._netrc: netrc_mod.netrc | None | bool = False  # False = not loaded yet

    def site_for(self, host: str) -> tuple[str, Site] | None:
        host = host.lower().rstrip(".")
        best = None
        for key, site in self.s.sites.items():
            k = key.lower().strip(".")
            if host == k or host.endswith("." + k):
                if best is None or len(k) > len(best[0]):  # most specific entry wins
                    best = (k, site)
        return best

    def _netrc_auth(self, host: str) -> tuple[str, str] | None:
        if not self.s.netrc:
            return None
        if self._netrc is False:
            try:
                self._netrc = netrc_mod.netrc(os.environ.get("NETRC"))
            except (FileNotFoundError, netrc_mod.NetrcParseError, OSError):
                self._netrc = None
        if not self._netrc:
            return None
        a = self._netrc.authenticators(host)
        return (a[0] or "", a[2] or "") if a and (a[0] or a[2]) else None

    def _has_cookies(self, host: str) -> bool:
        return any(host == c.domain.lstrip(".") or host.endswith("." + c.domain.lstrip(".")) for c in self.jar)

    def credentials(self, url: str) -> tuple[dict[str, str], httpx.Auth | None, bool]:
        """(headers, auth, has_credentials) for this URL's host. Only https gets secrets, except localhost."""
        u = urlsplit(url)
        host = u.hostname or ""
        found = self.site_for(host)
        headers: dict[str, str] = {}
        auth = None
        if found:
            _, site = found
            headers.update(site.headers)
            pw = site.password if site.password is not None else _env(site.password_env, "password", host)
            if site.username is not None or pw is not None:
                auth = httpx.BasicAuth(site.username or "", pw or "")
            token = site.bearer if site.bearer is not None else _env(site.bearer_env, "token", host)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif na := self._netrc_auth(host):
            auth = httpx.BasicAuth(*na)
        has = bool(headers or auth) or self._has_cookies(host)
        if has and u.scheme != "https" and host not in ("localhost", "127.0.0.1", "::1"):
            raise FetchError(f"refusing to send credentials for {host} over plain http; use the https URL")
        return headers, auth, has

    def get(self, url: str) -> tuple[str, str, bool]:
        """(final url, html, credentials used)."""
        used = False
        for _ in range(MAX_REDIRECTS + 1):
            headers, auth, has = self.credentials(url)
            used |= has
            try:
                r = self.client.get(url, headers=headers, auth=auth)
            except httpx.HTTPError as e:
                raise FetchError(f"could not fetch {url}: {type(e).__name__}: {e}") from None
            if r.is_redirect and "location" in r.headers:
                url = urljoin(str(r.url), r.headers["location"])
                continue
            if r.status_code in (401, 403, 407):
                host = urlsplit(url).hostname
                hint = ("the credentials were rejected" if has else
                        f'add credentials under [ingest.url.sites."{host}"] in incant.local.toml')
                raise FetchError(f"{url}: HTTP {r.status_code} (login required?) — {hint}")
            if r.status_code >= 400:
                raise FetchError(f"{url}: HTTP {r.status_code}")
            return str(r.url), r.text, used
        raise FetchError(f"{url}: more than {MAX_REDIRECTS} redirects")


def ingest_url(url: str, fetcher: Fetcher | None = None) -> Ingested:
    fetcher = fetcher or Fetcher(UrlSettings())
    final, html, used = fetcher.get(url)
    host = urlsplit(final).hostname
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    words = len(text.split())
    if _PASSWORD_FIELD.search(html) and words < LOGIN_PAGE_MAX_WORDS:
        why = ("the credentials/cookies didn't log in (expired session?)" if used else
               f'add credentials or cookies under [ingest.url.sites."{host}"] in incant.local.toml')
        raise FetchError(f"{url} returned a login page ({final}) — {why}")
    notes = ["URL ingest reads a single page only."]
    if words < fetcher.s.min_words and fetcher.s.embedded:
        if found := embedded_text(html, fetcher.s.embedded_min_words):
            text = found
            words = len(text.split())
            notes.append(f"text taken from data embedded in the page's scripts ({words:,} words); check it is "
                         "the article, not a comment or summary")
    if words < fetcher.s.min_words:
        gate = _GATE.search(text)  # in the extracted text: what the page shows instead of the content
        label = " ".join(gate.group(0).split()) if gate else ""
        if gate and used:
            what = (f'a gate page ("{label}") even with your credentials/cookies — '
                    "the session may have expired; export fresh cookies")
        elif gate:
            what = (f'a gate page ("{label}") — if your browser session sees the text, '
                    f'export its cookies and set cookies = "…" under [ingest.url.sites."{host}"] in incant.local.toml')
        elif used:
            what = ("no article text in the page or its embedded data although your credentials/cookies were sent: "
                    "the page probably fetches its text with JavaScript after loading, which needs a browser")
        else:
            what = ("no readable text in the page or its embedded data: it may fetch its content with JavaScript "
                    "after loading, or need a login")
        raise FetchError(f"{url}: only {words} words extracted — {what}. Nothing ingested.")
    md = trafilatura.extract_metadata(html)
    if len(notes) == 1:  # trafilatura emits one paragraph per line; make them blank-line separated
        text = "\n\n".join(clean_unicode(text).splitlines())
    else:  # embedded text already has blank lines between paragraphs
        text = clean_unicode(text)
    return Ingested(
        title=(md.title if md and md.title else url),
        author=(md.author if md else None),
        paragraphs=paragraphs_from_plain(text),
        source_format="url",
        warnings=notes,
    )


class UrlIngest(IngestMethod):
    """A single web page, main text extracted with trafilatura; per-site credentials supported."""

    Settings = UrlSettings
    settings: UrlSettings

    @classmethod
    def accepts(cls, source: str) -> bool:
        return source.startswith(("http://", "https://"))

    def fingerprint(self) -> str:
        """Secrets stay out of pipeline.json: only which sites have credentials counts."""
        import hashlib
        import json

        visible = self.settings.model_dump(exclude={"sites"}) | {"sites": sorted(self.settings.sites)}
        blob = json.dumps([self.stage, self.name, visible], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def check(self) -> list[tuple[bool, str]]:
        out = []
        for host, site in self.settings.sites.items():
            for var in (site.password_env, site.bearer_env):
                if var:
                    out.append((var in os.environ, f"{host}: environment variable {var}"))
            if site.cookies:
                p = Path(site.cookies).expanduser()
                out.append((p.is_file(), f"{host}: cookies file {p}"))
        return out

    def load(self, source: str) -> Ingested:
        return ingest_url(source, Fetcher(self.settings))
