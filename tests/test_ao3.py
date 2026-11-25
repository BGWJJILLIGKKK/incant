"""ingest=ao3: Archive of Our Own works — multi-chapter works as one book, canonical source, adult
unlock, header metadata (no network: fetch_page and the fetcher are faked with AO3-shaped fixtures)."""

import json
from pathlib import Path

import pytest

from incant.cli import main
from incant.config import load_config
from incant.pipeline import _auto, discover, instantiate, load_method_class, parent_methods
from incant.stages.ingest.ao3.ao3 import Ao3Ingest, Ao3Settings, adult_cookie, work_meta, work_of
from incant.stages.ingest.base import Ingested
from incant.stages.ingest.url import url as url_mod
from incant.stages.ingest.url.url import Fetcher, UrlSettings

INGEST = {s.name: s for s in discover()}["ingest"]

WORK = "https://archiveofourown.org/works/123"

WORK_HTML = """<!DOCTYPE html>
<html><head><title>Test Work by tester ⋆ AO3</title></head>
<body><div id="main"><div id="workskin"><div id="header">
  <h2 class="title heading">Test Work</h2>
  <h3 class="byline heading">
    <a rel="author" href="/users/tester/pseuds/tester">tester</a>
  </h3>
  <div class="summary module"><h3 class="heading">Summary:</h3>
    <blockquote class="userstuff">A blurb about the work.</blockquote>
  </div>
  <div class="notes module"><h3 class="heading">Notes:</h3>
    <blockquote class="userstuff">A note that must not become the blurb.</blockquote>
  </div>
  <dl class="stats"><dd class="chapters">3/? Chapters</dd></dl>
</div>
<div id="chapters"><div class="chapter" id="chapter-1">
  <div class="userstuff module" role="article"><p>First chapter text.</p></div>
</div></div></div></div></body></html>"""

NAV_HTML = """<html><body><ol class="chapter index group">
  <li><a href="/works/123/chapters/1001">1. One</a> <span class="datetime">(2024-01-01)</span></li>
  <li><a href="/works/123/chapters/1002">2. Two</a> <span class="datetime">(2024-01-02)</span></li>
  <li><a href="/works/123/chapters/1003">3. Three</a> <span class="datetime">(2024-01-03)</span></li>
</ol></body></html>"""

NAV_ONE = """<html><body><ol class="chapter index group">
  <li><a href="/works/999/chapters/555">1. Whole Thing</a> <span class="datetime">(2024-01-01)</span></li>
</ol></body></html>"""


def ao3():
    return Ao3Ingest(Ao3Settings(render="never", part_delay_s=0), load_config(Path("/nonexistent.toml")))


def test_accepts_only_work_urls():
    ok = ["https://archiveofourown.org/works/12345",
          "https://archiveofourown.org/works/12345/chapters/67890",
          "https://www.archiveofourown.org/collections/c/works/12345",
          "http://archiveofourown.org/works/12345/"]
    bad = ["https://archiveofourown.org/series/12",
           "https://archiveofourown.org/users/x/pseuds/x",
           "https://example.org/s/123",
           "https://example.com/works/123",
           "/tmp/books/works/123"]
    assert all(Ao3Ingest.accepts(u) for u in ok), [u for u in ok if not Ao3Ingest.accepts(u)]
    assert not any(Ao3Ingest.accepts(u) for u in bad), [u for u in bad if Ao3Ingest.accepts(u)]
    assert _auto(INGEST, "https://archiveofourown.org/works/123") == "ao3"  # more specific than url
    assert _auto(INGEST, "https://example.com/a") == "url"


def test_inherits_from_url(tmp_path):
    cls = load_method_class(INGEST, "ao3")
    assert parent_methods(INGEST, cls) == ["url"]
    conf = tmp_path / "incant.toml"
    conf.write_text('[ingest.url]\ntimeout_s = 12\n[ingest.ao3]\nmax_parts = 5\n')
    m = instantiate(load_config(conf), INGEST, "ao3")
    assert m.settings.timeout_s == 12 and m.settings.max_parts == 5  # [ingest.url] applies to ao3


def test_work_of_and_meta_parsing():
    assert work_of(WORK + "/chapters/1002") == ("https://archiveofourown.org", "/works/123")
    assert work_of("https://archiveofourown.org/collections/c/works/77") == ("https://archiveofourown.org", "/works/77")
    assert work_of("https://archiveofourown.org/series/5") is None
    assert work_meta(WORK_HTML) == ("Test Work", "tester", "A blurb about the work.")  # not the author's note
    assert work_meta('<h2 class="title heading">T</h2><blockquote class="userstuff summary">Old layout</blockquote>') \
        == ("T", None, "Old layout")
    assert work_meta("<html><body>x</body></html>") == (None, None, None)


def test_adult_cookie():
    f = Fetcher(UrlSettings())
    adult_cookie(f, "archiveofourown.org")
    c = next(c for c in f.jar if c.name == "view_adult")
    assert c.value == "true" and c.domain == "archiveofourown.org" and not c.domain_specified  # also www.*


@pytest.fixture
def fake_ao3(monkeypatch):
    def fake_get(self, url):
        assert "/navigate" in url, url
        return url, NAV_HTML, False

    def fake_fetch(url, fetcher=None):
        # AO3 serves the work header on every chapter page, so work_meta works on all of them
        if url.split("?")[0] == WORK:
            return url_mod.Page(url, WORK_HTML, Ingested(title="Test Work by tester ⋆ AO3",
                                                         paragraphs=["Chapter Text", "First chapter text."],
                                                         source_format="url"))
        return url_mod.Page(url, WORK_HTML, Ingested(title="t", paragraphs=["Chapter Text",
                                                                            f"Text of {url.rsplit('/', 1)[-1]}."],
                                                     source_format="url"))

    monkeypatch.setattr(url_mod.Fetcher, "get", fake_get)
    monkeypatch.setattr(url_mod, "fetch_page", fake_fetch)


def test_multi_chapter_work_becomes_one_book(fake_ao3):
    ing = ao3().load(WORK + "/chapters/1002")
    assert [p for p in ing.paragraphs if p.startswith("# ")] == ["# One", "# Two", "# Three"]
    assert "Chapter Text" not in ing.paragraphs  # AO3's section label is stripped
    assert ing.source == WORK  # a chapter URL is the same book as the work page
    assert (ing.title, ing.author, ing.description) == ("Test Work", "tester", "A blurb about the work.")
    assert any("3 parts as chapters" in w for w in ing.warnings)


def test_navigate_url_gets_the_adult_param(fake_ao3, monkeypatch):
    seen = []
    real_get = url_mod.Fetcher.get

    def spy(self, url):
        seen.append(url)
        return real_get(self, url)

    monkeypatch.setattr(url_mod.Fetcher, "get", spy)
    ao3().load(WORK)
    assert seen == [WORK + "/navigate?view_adult=true"]
    seen.clear()
    m = ao3()
    m.settings.view_adult = False
    m.load(WORK)
    assert seen == [WORK + "/navigate"]


def test_single_chapter_work_stays_one_page(monkeypatch):
    def fake_get(self, url):
        return url, NAV_ONE, False

    def fake_fetch(url, fetcher=None):
        return url_mod.Page(url, WORK_HTML.replace("Test Work", "Single Work"),
                            Ingested(title=url, paragraphs=["All of it."], source_format="url"))

    monkeypatch.setattr(url_mod.Fetcher, "get", fake_get)
    monkeypatch.setattr(url_mod, "fetch_page", fake_fetch)
    ing = ao3().load("https://archiveofourown.org/works/999/chapters/555")
    assert ing.paragraphs == ["All of it."]
    assert ing.source == "https://archiveofourown.org/works/999"  # canonical, whatever URL named it
    assert ing.title == "Single Work"


def test_cli_end_to_end(fake_ao3, tmp_path, capsys):
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n[pipeline]\nparse = "basic"\nattribute = "narrator"\n'
                    'normalize = "rules"\nsynthesize = "dummy"\n')
    assert main([WORK, "-c", str(conf), "--until", "parse"]) == 0
    out = capsys.readouterr().out
    assert "ingest=ao3 (auto)" in out
    meta = json.loads((tmp_path / "work" / "test-work" / "meta.json").read_text())
    assert meta["source"] == WORK and meta["title"] == "Test Work" and meta["author"] == "tester"
    chapters = json.loads((tmp_path / "work" / "test-work" / "chapters.json").read_text())
    assert [c["title"] for c in chapters["chapters"]] == ["One", "Two", "Three"]


def test_plain_http_input_upgraded(fake_ao3):
    """http://archiveofourown.org/works/123 works and doesn't fail on credential checks."""
    ing = ao3().load("http://archiveofourown.org/works/123")
    assert ing.source == WORK


def test_single_chapter_navigate_404_falls_back_cleanly(monkeypatch):
    def fake_get(self, url):
        raise url_mod.FetchError("HTTP 404")

    def fake_fetch(url, fetcher=None):
        return url_mod.Page(url, WORK_HTML.replace("Test Work", "Solo"),
                            Ingested(title=url, paragraphs=["Solo chapter."], source_format="url"))

    monkeypatch.setattr(url_mod.Fetcher, "get", fake_get)
    monkeypatch.setattr(url_mod, "fetch_page", fake_fetch)
    ing = ao3().load("https://archiveofourown.org/works/888")
    assert ing.paragraphs == ["Solo chapter."]
    assert ing.title == "Solo"


def test_author_deduplication():
    html = '''
    <h2 class="title heading">Shared</h2>
    <div class="header"><h3 class="heading"><a rel="author">Tester</a></h3></div>
    <div class="chapter"><div class="header"><a rel="author">Tester</a></div></div>
    '''
    title, author, _ = work_meta(html)
    assert title == "Shared"
    assert author == "Tester"
