"""ingest=ebook: other formats converted to EPUB (here by a stand-in converter), then read by epub."""

import os
import stat

import pytest

from incant.cli import main
from incant.pipeline import _auto, discover
from incant.stages.ingest.ebook import ebook as ebook_mod
from test_pipeline import make_epub

INGEST = {s.name: s for s in discover()}["ingest"]


def fake_tool(tmp_path, name, fixture):
    """A converter script that 'converts' by copying a prepared EPUB to its output argument."""
    exe = tmp_path / "bin" / name
    exe.parent.mkdir(exist_ok=True)
    out_arg = '"$3"' if name == "pandoc" else '"$2"'  # pandoc IN -o OUT / ebook-convert IN OUT
    exe.write_text(f'#!/bin/sh\ncp "{fixture}" {out_arg}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_auto_detection(tmp_path):
    doc = tmp_path / "story.docx"
    doc.write_bytes(b"PK")
    bm = tmp_path / "bookmarks.html"
    bm.write_text("<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><DT><A HREF='https://x.test/a'>A</A></DL>")
    page = tmp_path / "page.html"
    page.write_text("<html><body><p>Hi.</p></body></html>")
    assert _auto(INGEST, str(doc)) == "ebook" and _auto(INGEST, str(page)) == "ebook"
    assert _auto(INGEST, str(bm)) == "bookmark"


def test_docx_through_pandoc(tmp_path, monkeypatch, capsys):
    fixture = tmp_path / "fixture.epub"
    make_epub(fixture)
    fake_tool(tmp_path, "pandoc", fixture)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    doc = tmp_path / "test.docx"
    doc.write_bytes(b"PK")
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n')
    assert main([str(doc), "--until", "parse", "-c", str(conf)]) == 0
    out = capsys.readouterr().out
    assert "ingest=ebook" in out and "converted from .docx with pandoc" in out
    assert (tmp_path / "work" / "test-book" / "chapters.json").exists()  # read by epub: title from the EPUB


def test_kindle_needs_calibre(tmp_path, monkeypatch):
    monkeypatch.setattr(ebook_mod.shutil, "which", lambda name: "/usr/bin/pandoc" if name == "pandoc" else None)
    m = ebook_mod.EbookIngest(ebook_mod.EbookSettings(), None)
    with pytest.raises(RuntimeError, match=r"needs Calibre \(apt install calibre\)"):
        m.tool(".azw3")
    assert m.tool(".odt")[0] == "pandoc"
