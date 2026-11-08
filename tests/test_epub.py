"""The EPUB container reader (zipfile + lxml): metadata, spine, TOC from NCX or nav, covers, broken files."""

import zipfile

import pytest

from incant.stages.ingest.epub.container import EpubError, EpubFile
from incant.stages.ingest.epub.epub import ingest_epub
from test_pipeline import make_epub, write_epub

DOCS = [("text/one.xhtml", "First Part", "<h2>One</h2><p>Alpha.</p>"),
        ("text/two.xhtml", "Second Part", "<p>Beta, no heading here.</p>")]


def test_reads_the_fixture_like_before(tmp_path):
    make_epub(tmp_path / "b.epub")
    ing = ingest_epub(tmp_path / "b.epub")
    assert (ing.title, ing.author, ing.language) == ("Test Book", "A. Writer", "en")
    assert ing.paragraphs[:2] == ["by A. Writer", "# Chapter One"] and "Second *chapter* text." in ing.paragraphs
    assert not any("caption" in p or "skip me" in p or "Boilerplate" in p for p in ing.paragraphs)
    assert "The end of one." in ing.paragraphs  # image drop-cap kept


@pytest.mark.parametrize("ncx,nav", [(True, False), (False, True), (True, True)])
def test_toc_titles_from_ncx_or_nav(tmp_path, ncx, nav):
    write_epub(tmp_path / "b.epub", "T", "A", DOCS, ncx=ncx, nav=nav)
    book = EpubFile.open(tmp_path / "b.epub")
    assert book.toc == {"text/one.xhtml": "First Part", "text/two.xhtml": "Second Part"}
    assert [d.href for d in book.documents()] == ["text/one.xhtml", "text/two.xhtml"]  # nav left out
    assert "# Second Part" in ingest_epub(tmp_path / "b.epub").paragraphs  # untitled document named by the TOC


def test_package_at_the_zip_root_and_links_relative_to_a_nav_in_a_subfolder(tmp_path):
    p = tmp_path / "b.epub"
    write_epub(p, "T", "A", DOCS, ncx=False, nav=False, opf_dir="",
               extra_manifest='<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    with zipfile.ZipFile(p, "a") as z:
        z.writestr("nav/toc.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol>'
                   '<li><a href="../text/one.xhtml#start">First Part</a></li></ol></nav></body></html>')
    assert EpubFile.open(p).toc == {"text/one.xhtml": "First Part"}


def test_cover_from_properties_or_meta(tmp_path):
    p = tmp_path / "b.epub"
    write_epub(p, "T", "A", DOCS, extra_meta='<meta name="cover" content="img"/>',
               extra_manifest='<item id="img" href="images/front.jpeg" media-type="image/jpeg"/>')
    with zipfile.ZipFile(p, "a") as z:
        z.writestr("EPUB/images/front.jpeg", b"\xff\xd8jpeg")
    assert EpubFile.open(p).cover() == ("jpg", b"\xff\xd8jpeg")
    q = tmp_path / "c.epub"
    write_epub(q, "T", "A", DOCS, extra_manifest='<item id="c" href="c.png" media-type="image/png" properties="cover-image"/>')
    with zipfile.ZipFile(q, "a") as z:
        z.writestr("EPUB/c.png", b"png")
    assert EpubFile.open(q).cover() == ("png", b"png")


def test_missing_and_broken_files(tmp_path):
    p = tmp_path / "b.epub"
    write_epub(p, "T", "A", DOCS, extra_manifest='<item id="gone" href="gone.xhtml" media-type="application/xhtml+xml"/>')
    assert len(EpubFile.open(p).documents()) == 2  # manifest entries without a file are skipped
    (tmp_path / "x.epub").write_text("not a zip")
    with pytest.raises(EpubError, match="not a zip"):
        EpubFile.open(tmp_path / "x.epub")
    with zipfile.ZipFile(tmp_path / "y.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    with pytest.raises(EpubError, match="container.xml"):
        EpubFile.open(tmp_path / "y.epub")


def test_no_external_entities(tmp_path):
    p = tmp_path / "b.epub"
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    write_epub(p, "&xxe;", "A", DOCS)
    with zipfile.ZipFile(p) as z:
        files = {n: z.read(n) for n in z.namelist()}
    opf = files["EPUB/content.opf"].decode().replace(
        '<?xml version="1.0"?>', f'<?xml version="1.0"?><!DOCTYPE package [<!ENTITY xxe SYSTEM "file://{secret}">]>')
    files["EPUB/content.opf"] = opf.encode()
    with zipfile.ZipFile(p, "w") as z:
        for n, data in files.items():
            z.writestr(n, data)
    assert "TOPSECRET" not in (EpubFile.open(p).title or "")
