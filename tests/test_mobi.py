"""ingest=mobi: Kindle's old MOBI 6 HTML -> paragraphs; auto-detection and inheritance (child of ebook)."""

import importlib.util

import pytest

from incant.pipeline import _auto, discover, load_method_class, parent_methods
from incant.stages.ingest.mobi.mobi import mobi6_paragraphs

INGEST = {s.name: s for s in discover()}["ingest"]

MOBI6 = """<html><head><guide><reference type="toc" title="Table of Contents" filepos=0000226437 /></guide></head><body>
<p align="center"><font size="7">The Project Gutenberg eBook of A Tale</font></p>
<p>*** START OF THE PROJECT GUTENBERG EBOOK A TALE ***</p>
<p align="center"><font size="7">Contents</font></p>
<table><tr><td><a href="#filepos1"><u>CHAPTER I.</u></a></td><td>The Start</td></tr></table>
<mbp:pagebreak></mbp:pagebreak>
<p height="5em" align="center"><font size="6">CHAPTER I. The Start</font></p>
<p height="2em" align="justify">It was a <i>dark</i> night. "Who is there?" asked Ann.</p>
<p align="justify">Nobody answered.</p>
<mbp:pagebreak></mbp:pagebreak>
<p align="center"><font size="6">CHAPTER II. The End</font></p>
<p>The sun rose.</p>
<p>*** END OF THE PROJECT GUTENBERG EBOOK A TALE ***</p>
<p>Licence text that should not be read aloud.</p>
</body></html>"""


def test_mobi6_html_to_paragraphs():
    paras = mobi6_paragraphs(MOBI6)
    assert paras[0] == "# CHAPTER I. The Start"
    assert paras[1] == 'It was a dark night. "Who is there?" asked Ann.' and paras[2] == "Nobody answered."
    assert "# CHAPTER II. The End" in paras and paras[-1] == "The sun rose."
    assert not any("Gutenberg" in p or "Licence" in p or "Contents" in p for p in paras)


def test_mobi_is_a_child_of_ebook_and_wins_auto_detection(tmp_path):
    cls = load_method_class(INGEST, "mobi")
    assert parent_methods(INGEST, cls) == ["ebook", "epub"]
    book = tmp_path / "book.azw3"
    book.write_bytes(b"BOOKMOBI")
    expected = "mobi" if importlib.util.find_spec("mobi") else "ebook"  # without the package: Calibre via ebook
    assert _auto(INGEST, str(book)) == expected


@pytest.mark.skipif(importlib.util.find_spec("mobi") is None, reason="mobi package not installed")
def test_damaged_file_gives_a_clear_error(tmp_path):
    from incant.stages.ingest.ebook.ebook import EbookSettings
    from incant.stages.ingest.mobi.mobi import MobiIngest

    bad = tmp_path / "broken.mobi"
    bad.write_bytes(b"not a kindle book")
    with pytest.raises(RuntimeError, match="could not unpack broken.mobi"):
        MobiIngest(EbookSettings(), None).load(str(bad))
