"""Finished books go to one tree: <base>/<author>/<title>/<title>.<ext>."""

import json
import shutil
from pathlib import Path

import pytest

from incant.cli import main
from incant.schemas import BookMeta
from incant.stages.ingest.base import unique_slug
from incant.stages.ingest.url.url import split_byline
from incant.stages.output.base import book_name, safe_name, tree_path
from test_pipeline import HAVE_FFMPEG, make_epub


def meta(title, author=None, slug=None):
    from incant.normalize import slugify

    return BookMeta(slug=slug or slugify(title), title=title, author=author, source="x", source_format="txt")


def test_tree_path_names():
    assert tree_path(Path("o"), meta("Pride and Prejudice", "Jane Austen"), ".m4b") == \
        Path("o/Jane Austen/Pride and Prejudice/Pride and Prejudice.m4b")
    assert tree_path(Path("o"), meta("Untitled Notes"), ".m4b").parts[1] == "Unknown Author"
    assert safe_name('AC/DC: "Back" in <Black>?') == "AC DC Back in Black"
    assert safe_name("...") == "Untitled" and len(safe_name("x" * 500)) == 120
    assert book_name(meta("Post", slug="post-2")) == "Post (2)"  # a different source with the same title
    assert book_name(meta("Chapter 2", slug="chapter-2")) == "Chapter 2"


def test_split_byline():
    assert split_byline("A Quiet Harbour by Mara Vale - StoryHub", None, None, "storyhub.example") == \
        ("A Quiet Harbour", "Mara Vale")
    assert split_byline("Stand by Me by Stephen King", None, None, "x.org") == ("Stand by Me", "Stephen King")
    assert split_byline("Passed by the river", None, None, "x.org") == ("Passed by the river", None)
    assert split_byline("Passed by mara - StoryHub", None, None, "storyhub.example") == ("Passed", "mara")
    assert split_byline("News | Politics - The Guardian", "J. Doe", "The Guardian", "www.theguardian.com") == \
        ("News | Politics", "J. Doe")
    assert split_byline("Hello - World", None, None, "x.org") == ("Hello - World", None)


def test_same_source_keeps_its_book_when_the_title_changes(tmp_path):
    (tmp_path / "old-title").mkdir()
    (tmp_path / "old-title" / "meta.json").write_text(json.dumps(
        {"slug": "old-title", "title": "Old Title", "source": "https://a", "source_format": "url"}))
    assert unique_slug(tmp_path, "new-title", "https://a") == "old-title"
    assert unique_slug(tmp_path, "new-title", "https://b") == "new-title"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_book_lands_in_output_tree(tmp_path):
    src = tmp_path / "t.epub"
    make_epub(src)
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\noutput_dir = "library"\n[pipeline]\nparse = "basic"\n'
                    'attribute = "narrator"\nnormalize = "rules"\nsynthesize = "dummy"\n')
    assert main([str(src), "-c", str(conf)]) == 0
    out = tmp_path / "library" / "A. Writer" / "Test Book" / "Test Book.m4b"
    assert out.exists() and not (tmp_path / "work" / "test-book" / "out").exists()
    rec = json.loads((tmp_path / "work" / "test-book" / "output.json").read_text())
    assert rec == {"files": [str(out.resolve())]}

    shutil.rmtree(tmp_path / "library")
    assert main(["test-book", "-c", str(conf), "-o", str(tmp_path / "elsewhere")]) == 0  # -o DIR moves the tree base
    assert (tmp_path / "elsewhere" / "A. Writer" / "Test Book" / "Test Book.m4b").exists()


def test_catalog_author_names_are_put_in_reading_order():
    from incant.stages.ingest.base import normalize_author as n

    assert n("Dickens, Charles, 1812-1870") == "Charles Dickens"
    assert n("Shelley, Mary (1797-1851)") == "Mary Shelley"
    assert n("Stevenson, Robert Louis, 1850-1894") == "Robert Louis Stevenson"
    assert n("Mara Vale") == "Mara Vale" and n("Smith, John; Doe, Jane") == "Smith, John; Doe, Jane"
    assert n("  ") is None and n(None) is None
