import threading
from http.server import ThreadingHTTPServer
import httpx
import pytest

from incant.config import Config, PathsConfig
from incant.pipeline import discover, instantiate
from incant.review.server import Book, make_handler
from incant.schemas import Cast, Chapter, ChapterList, Span, SpanKind
from incant.workdir import WorkDir


@pytest.fixture
def review_server(tmp_path):
    cfg = Config(paths=PathsConfig(work_dir=tmp_path))
    # Dummy synthesizer backend
    stage = {s.name: s for s in discover()}["synthesize"]
    synth = instantiate(cfg, stage, "dummy")

    wd = WorkDir(tmp_path / "book")
    wd.root.mkdir(parents=True, exist_ok=True)
    ch = Chapter(id="0001", index=1, title="Chapter One", file="0001.md", word_count=50)
    wd.write_chapters(ChapterList(method="headings", chapters=[ch]))
    (wd.root / "0001.md").write_text('"Hello world," said Alice.\n')
    spans = [
        Span(id="0001-00001", chapter_id="0001", para=0, kind=SpanKind.dialogue, text='"Hello world,"', speaker="alice"),
        Span(id="0001-00002", chapter_id="0001", para=0, kind=SpanKind.narration, text=' said Alice.', speaker="narrator"),
    ]
    wd.write_spans("0001", spans)
    cast = Cast(roles={"alice": "dummy_voice"})
    wd.write_cast(cast)

    book = Book(cfg, wd)
    # Monkeypatch book.synth to return dummy
    book.synth = lambda: synth
    book.voices = synth.voices()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(book))
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", wd, book
    server.shutdown()
    server.server_close()


def test_review_render_span_audition(review_server):
    url, wd, book = review_server
    resp = httpx.post(f"{url}/api/render", json={"span_id": "0001-00001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["span_id"] == "0001-00001"
    assert "audio_url" in data
    # Check that audio file is served
    audio_resp = httpx.get(f"{url}{data['audio_url']}")
    assert audio_resp.status_code == 200
    assert audio_resp.headers["Content-Type"] == "audio/wav"


def test_review_render_chapter(review_server):
    url, wd, book = review_server
    resp = httpx.post(f"{url}/api/render", json={"chapter_id": "0001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["chapter_id"] == "0001"
    assert data["segments"] >= 1
    # Check chapter wav served
    audio_resp = httpx.get(f"{url}{data['audio_url']}")
    assert audio_resp.status_code == 200
    assert audio_resp.headers["Content-Type"] == "audio/wav"
    # Check manifest updated
    assert wd.manifest_path.exists()
    m = wd.read_manifest()
    assert any(c.chapter_id == "0001" for c in m.chapters)


def test_review_render_invalid_request(review_server):
    url, _, _ = review_server
    resp = httpx.post(f"{url}/api/render", json={})
    assert resp.status_code == 400
