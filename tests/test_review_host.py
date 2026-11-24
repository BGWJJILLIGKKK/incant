"""--review bind address: --host, else $INCANT_REVIEW_HOST (compose.desktop.yaml), else [review] host."""

import pytest

from incant.cli import main
from incant.review import server

CONF = '[paths]\nwork_dir = "work"\n'


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.delenv("INCANT_DATA", raising=False)
    monkeypatch.delenv("INCANT_REVIEW_HOST", raising=False)
    conf = tmp_path / "incant.toml"
    conf.write_text(CONF)
    src = tmp_path / "story.txt"
    src.write_text('Chapter 1\n\n"Hello," said Ann.\n')
    assert main([str(src), "-c", str(conf), "--until", "ingest"]) == 0
    calls = []
    monkeypatch.setattr(server, "serve", lambda cfg, wd, host, port, published=False: calls.append((host, published)))
    return conf, calls


@pytest.mark.parametrize("env, args, expected", [
    (None, [], ("127.0.0.1", False)),
    ("0.0.0.0", [], ("0.0.0.0", True)),
    ("0.0.0.0", ["--host", "127.0.0.1"], ("127.0.0.1", False)),
])
def test_review_host(book, monkeypatch, env, args, expected):
    conf, calls = book
    if env:
        monkeypatch.setenv("INCANT_REVIEW_HOST", env)
    assert main(["story", "-c", str(conf), "--review", *args]) == 0
    assert calls == [expected]


def test_published_banner(tmp_path, monkeypatch, capsys):
    """In the container the page is opened at the host's localhost, not 0.0.0.0. Outside one, a LAN bind needs
    an authenticating proxy ([review] public_domains + trusted_proxy_ips), as for the dashboard."""
    from incant.config import Config

    class FakeServer:
        def __init__(self, addr, handler):
            self.addr = addr

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    class FakeWd:
        def read_meta(self):
            return type("M", (), {"title": "Story"})()

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server, "Book", lambda cfg, wd: None)
    server.serve(Config(), FakeWd(), "0.0.0.0", 8765, published=True)
    assert "http://localhost:8765/" in capsys.readouterr().out
    with pytest.raises(ValueError, match="LAN binding requires"):
        server.serve(Config(), FakeWd(), "0.0.0.0", 8765)


def test_review_page_checks_host_origin_and_body(book):
    """A page the user visits (cross-site POST, text/plain to skip the preflight) or a rebound DNS name can't
    drive the review page; responses can't be framed."""
    import http.client
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from incant.config import load_config
    from incant.workdir import WorkDir

    conf, _ = book
    cfg = load_config(conf)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(server.Book(cfg, WorkDir.for_slug(cfg.paths.work_dir, "story"))))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def request(method, path, headers, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    try:
        local = {"Host": f"localhost:{port}"}
        status, headers, _ = request("GET", "/", local)
        assert status == 200 and headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert request("GET", "/", {"Host": f"evil.example:{port}"})[0] == 403  # DNS rebinding
        lexicon = json.dumps({"Ann": "Ahn"})
        cross = {**local, "Origin": "https://evil.example", "Content-Type": "text/plain"}
        assert request("POST", "/api/lexicon", cross, lexicon)[0] == 403
        same = {**local, "Origin": f"http://localhost:{port}"}
        assert request("POST", "/api/lexicon", {**same, "Content-Type": "text/plain"}, lexicon)[0] == 400
        assert request("POST", "/api/lexicon", {**same, "Content-Type": "application/json"}, lexicon)[0] == 200
        try:  # refused unread: a 400, or the connection closes while the client is still sending
            assert request("POST", "/api/lexicon", {**same, "Content-Type": "application/json"}, "x" * (3 * 1024 * 1024))[0] == 400
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        httpd.shutdown()
