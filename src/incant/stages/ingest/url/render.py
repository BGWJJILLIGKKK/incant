"""Render a page in headless Chromium (Playwright) for sites that fetch their text with JavaScript.

The browser gets the same per-site credentials as plain fetching, scoped the same way:
  * cookies from the configured cookies.txt files, added to the browser with their own domains;
  * Basic auth, bearer tokens and extra headers, injected per request, only into requests for the
    configured site (never into third-party scripts, CDNs or trackers the page loads).
Images, media and fonts are not downloaded.

Optional: `uv sync --extra browser && .venv/bin/playwright install chromium`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .url import Fetcher

SKIP_RESOURCES = {"image", "media", "font"}


class RenderUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _cookies(fetcher: Fetcher) -> list[dict]:
    out = []
    for c in fetcher.jar:
        d = {"name": c.name, "value": c.value or "", "domain": c.domain, "path": c.path or "/",
             "secure": bool(c.secure), "httpOnly": bool(c.has_nonstandard_attr("HttpOnly"))}
        if c.expires:
            d["expires"] = float(c.expires)
        out.append(d)
    return out


def _auth_headers(fetcher: Fetcher, url: str) -> dict[str, str]:
    """Headers this request may carry: the site's own credentials, or nothing."""
    from .url import FetchError

    try:
        headers, auth, _ = fetcher.credentials(url)
    except FetchError:  # e.g. credentials over plain http: send none
        return {}
    if auth is not None:
        req = next(auth.auth_flow(httpx.Request("GET", url)))
        headers = {**headers, "Authorization": req.headers["Authorization"]}
    return headers


def render(url: str, fetcher: Fetcher, timeout_s: float, settle_ms: int = 1500) -> tuple[str, str]:
    """(final url, rendered HTML) after the page's scripts have loaded its content."""
    if not available():
        raise RenderUnavailable("rendering needs Playwright: uv sync --extra browser && "
                                ".venv/bin/playwright install chromium")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    def route(r):
        req = r.request
        if req.resource_type in SKIP_RESOURCES:
            return r.abort()
        extra = _auth_headers(fetcher, req.url)
        return r.continue_(headers={**req.headers, **extra}) if extra else r.continue_()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(user_agent=fetcher.s.user_agent)
                ctx.add_cookies(_cookies(fetcher))
                page = ctx.new_page()
                page.route("**/*", route)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                try:  # wait for the content request(s); some pages never go fully idle (live sockets)
                    page.wait_for_load_state("networkidle", timeout=timeout_s * 1000)
                except PlaywrightError:
                    pass
                time.sleep(settle_ms / 1000)  # let the framework insert what it fetched
                return page.url, page.content()
            finally:
                browser.close()
    except PlaywrightError as e:
        from .url import FetchError

        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        if "Executable doesn't exist" in str(e):
            raise RenderUnavailable("Chromium isn't installed: .venv/bin/playwright install chromium") from None
        raise FetchError(f"could not render {url}: {msg}") from None
