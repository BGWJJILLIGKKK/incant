"""Browser rendering (real headless Chromium against a local server): a page that fetches its text with
JavaScript after loading, with credentials scoped to their own host."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from incant.stages.ingest.url import render as render_mod
from incant.stages.ingest.url.url import Fetcher, FetchError, UrlSettings, ingest_url

pytestmark = pytest.mark.skipif(not render_mod.available(), reason="playwright not installed")

STORY = "".join(f"<p>Paragraph {i}: the dragon walked down the quiet street, and the neighbours stared. "
                f"“Good morning,” she said. Nobody answered.</p>" for i in range(30))


@pytest.fixture
def server():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            seen.append((host, self.path, self.headers.get("Cookie"), self.headers.get("Authorization")))
            port = self.server.server_address[1]
            if self.path.startswith("/story"):
                body = (f'<html><head><title>JS Story</title></head><body><nav>Menu</nav><div id="root">Loading…</div>'
                        f'<script src="http://127.0.0.1:{port}/tracker.js"></script>'
                        '<script>fetch("/content").then(r => r.text()).then(t => {'
                        'document.getElementById("root").innerHTML = "<article>" + t + "</article>"; });</script>'
                        '</body></html>')
            elif self.path == "/content":
                if "sess=ok" not in (self.headers.get("Cookie") or ""):
                    self.send_response(403)
                    self.end_headers()
                    return
                body = STORY
            elif self.path == "/tracker.js":
                body = "/* third party */"
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if not self.path.endswith(".js") else "text/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], seen
    srv.shutdown()


def settings(tmp_path, **kw):
    os.environ["INCANT_TEST_RENDER_TOKEN"] = "tok"
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\nlocalhost\tFALSE\t/\tFALSE\t0\tsess\tok\n")
    return UrlSettings(netrc=False, render_timeout_s=20,
                       sites={"localhost": {"cookies": str(jar), "bearer_env": "INCANT_TEST_RENDER_TOKEN"}}, **kw)


def test_renders_javascript_loaded_text_with_scoped_credentials(server, tmp_path):
    port, seen = server
    ing = ingest_url(f"http://localhost:{port}/story", Fetcher(settings(tmp_path)))
    assert len(ing.paragraphs) >= 30 and ing.paragraphs[0].startswith("Paragraph 0:")
    assert any("rendered in a browser" in w for w in ing.warnings)
    content = [s for s in seen if s[1] == "/content"]
    assert content and all(c and "sess=ok" in c and a == "Bearer tok" for _, _, c, a in content)
    tracker = [s for s in seen if s[1] == "/tracker.js"]
    assert tracker and all(h == "127.0.0.1" and c is None and a is None for h, _, c, a in tracker)


def test_render_never_and_no_session(server, tmp_path):
    port, _ = server
    with pytest.raises(FetchError, match="page or its embedded data"):
        ingest_url(f"http://localhost:{port}/story", Fetcher(settings(tmp_path, render="never")))
    anon = UrlSettings(netrc=False, render_timeout_s=20)
    with pytest.raises(FetchError, match="browser render"):
        ingest_url(f"http://localhost:{port}/story", Fetcher(anon))
