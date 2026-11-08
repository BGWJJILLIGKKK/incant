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
    """In the container the page is opened at the host's localhost, not 0.0.0.0, and without the LAN warning."""
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
    server.serve(None, FakeWd(), "0.0.0.0", 8765, published=True)
    out = capsys.readouterr().out
    assert "http://localhost:8765/" in out and "anyone on the network" not in out
    server.serve(None, FakeWd(), "0.0.0.0", 8765)
    assert "anyone on the network" in capsys.readouterr().out
