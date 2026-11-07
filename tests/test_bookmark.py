"""Bookmark files as containers: parsing, detection, and batch expansion (no network: url ingest is faked)."""

import json

import pytest

from incant.cli import main
from incant.stages.ingest.base import Ingested, unique_slug
from incant.stages.ingest.bookmark.bookmark import BookmarkIngest
from incant.stages.ingest.url import url as url_mod

NETSCAPE = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks Menu</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000" LAST_MODIFIED="1700000001">Reading</H3>
    <DL><p>
        <DT><A HREF="https://example.com/story-1" ADD_DATE="1700000002" ICON="data:image/png;base64,AAA">Story &amp; One</A>
        <DT><A HREF="https://example.com/story-2#comments" ADD_DATE="1700000003">Story Two</A>
        <DD>A description line
        <DT><A HREF="javascript:void(0)">Bookmarklet</A>
        <DT><A HREF="place:sort=8&maxResults=10">Recent</A>
    </DL><p>
    <DT><A HREF="https://example.com/story-2">Story Two again</A>
    <DT><A HREF="http://example.org/untitled">Untitled</A>
</DL><p>
"""

CHROME = {"checksum": "x", "roots": {
    "bookmark_bar": {"children": [
        {"type": "url", "name": "A", "url": "https://a.example/1"},
        {"type": "folder", "name": "F", "children": [{"type": "url", "name": "B", "url": "https://b.example/2"}]},
    ], "name": "Bookmarks bar", "type": "folder"},
    "other": {"children": [], "name": "Other", "type": "folder"}}, "version": 1}

FIREFOX = {"guid": "root________", "title": "", "type": "text/x-moz-place-container", "children": [
    {"title": "menu", "type": "text/x-moz-place-container", "children": [
        {"title": "C", "type": "text/x-moz-place", "uri": "https://c.example/3"},
        {"title": "Sep", "type": "text/x-moz-place-separator"},
    ]}]}


CONF = ('[paths]\nwork_dir = "work"\n[pipeline]\nparse = "basic"\nattribute = "narrator"\nnormalize = "rules"\n'
        'synthesize = "dummy"\n')


def urls(path):
    return [(i.source, i.title) for i in BookmarkIngest.expand(str(path))]


def test_netscape_export(tmp_path):
    p = tmp_path / "bookmarks.html"
    p.write_text(NETSCAPE)
    assert BookmarkIngest.accepts(str(p))
    assert urls(p) == [  # http(s) only; duplicates (ignoring #fragment) keep the first title
        ("https://example.com/story-1", "Story & One"),
        ("https://example.com/story-2#comments", "Story Two"),
        ("http://example.org/untitled", "Untitled"),
    ]


def test_json_formats(tmp_path):
    for name, data, want in [
        ("chrome.json", CHROME, [("https://a.example/1", "A"), ("https://b.example/2", "B")]),
        ("firefox.json", FIREFOX, [("https://c.example/3", "C")]),
        ("list.json", [{"href": "https://d.example", "name": "D"}, {"url": "ftp://x"}], [("https://d.example", "D")]),
        ("wrapped.json", {"bookmarks": [{"url": "https://e.example", "title": ""}]}, [("https://e.example", None)]),
    ]:
        p = tmp_path / name
        p.write_text(json.dumps(data))
        assert BookmarkIngest.accepts(str(p)), name
        assert urls(p) == want, name


def test_ordinary_files_are_not_claimed(tmp_path):
    page = tmp_path / "chapter.html"
    page.write_text('<html><body><p>A story with <a href="https://x.example">a link</a>.</p></body></html>')
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "x", "homepage": "https://x.example", "repository": {"url": "https://g"}}))
    bad = tmp_path / "broken.json"
    bad.write_text("{not json")
    for p in (page, pkg, bad):
        assert not BookmarkIngest.accepts(str(p)), p.name
        assert BookmarkIngest.expand(str(p)) is None
    assert not BookmarkIngest.accepts("https://example.com/bookmarks.html")


def test_load_refuses_container(tmp_path):
    p = tmp_path / "b.html"
    p.write_text(NETSCAPE)
    with pytest.raises(RuntimeError, match="bookmark file"):
        BookmarkIngest(None, None).load(str(p))


def test_unique_slug(tmp_path):
    (tmp_path / "post").mkdir()
    (tmp_path / "post" / "meta.json").write_text(json.dumps(
        {"slug": "post", "title": "Post", "source": "https://a", "source_format": "url"}))
    assert unique_slug(tmp_path, "post", "https://a") == "post"  # same source: same book
    assert unique_slug(tmp_path, "post", "https://b") == "post-2"  # different source: new book
    assert unique_slug(tmp_path, "other", "https://b") == "other"


@pytest.fixture
def fake_web(monkeypatch):
    pages = {
        "https://example.com/story-1": ("Post", "First story text. “Hi,” said Ann."),
        "https://example.com/story-2#comments": ("Post", "Second story, same page title."),
        "http://example.org/untitled": (None, "A page with no title of its own."),
    }

    def fetch(url, fetcher=None):
        if url not in pages:
            raise RuntimeError(f"could not fetch {url}")
        title, body = pages[url]
        return url_mod.Page(url, "", Ingested(title=title or url, paragraphs=[body], source_format="url"))

    monkeypatch.setattr(url_mod, "fetch_page", fetch)
    return pages


def test_bookmark_file_runs_as_batch(tmp_path, fake_web, capsys):
    conf = tmp_path / "incant.toml"
    conf.write_text(CONF)
    bm = tmp_path / "bookmarks.html"
    bm.write_text(NETSCAPE + '<DT><A HREF="https://gone.example/">Gone</A>\n')

    assert main([str(bm), "-c", str(conf), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "batch: 4 books" in out and "ingest=url (auto)" in out and "[1/4] Story & One" in out

    rc = main([str(bm), "-c", str(conf), "--until", "parse"])
    out = capsys.readouterr().out
    assert rc == 1 and "batch finished: 3 ok, 1 failed" in out and "Gone" in out
    work = tmp_path / "work"
    # same page title from two pages: two books, not one overwritten; untitled page named from its bookmark
    assert sorted(p.name for p in work.iterdir()) == ["post", "post-2", "untitled"]
    assert json.loads((work / "untitled" / "meta.json").read_text())["title"] == "Untitled"

    # re-running reuses the same books
    assert main([str(bm), "-c", str(conf), "--until", "parse"]) == 1
    assert sorted(p.name for p in work.iterdir()) == ["post", "post-2", "untitled"]

    # naming the method explicitly works too; --review and --slug make no sense for many books
    assert main([str(bm), "bookmark", "-c", str(conf), "--dry-run"]) == 0
    assert main([str(bm), "-c", str(conf), "--review"]) == 2
    assert main([str(bm), "-c", str(conf), "--slug", "x"]) == 2


def test_bookmark_file_inside_batch_directory(tmp_path, fake_web, capsys):
    conf = tmp_path / "incant.toml"
    conf.write_text(CONF)
    d = tmp_path / "in"
    d.mkdir()
    (d / "a.txt").write_text("Title: Little Book\n\nChapter 1\n\nIt was short.\n\nChapter 2\n\nThe end came.\n")
    (d / "b.html").write_text(NETSCAPE)
    (d / "c.html").write_text("<html><body>not bookmarks</body></html>")
    assert main([str(d), "-c", str(conf), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "batch: 4 books" in out and "a.txt:" in out and "b.html [3/3] Untitled" in out
    assert "skip: c.html" in out
