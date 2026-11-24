"""Methods that inherit from other methods: settings, auto-detection, --list, url series, gutenberg."""


import pytest

from incant.cli import main
from incant.config import load_config
from incant.pipeline import _auto, discover, instantiate, parent_methods, load_method_class
from incant.stages.ingest.base import Ingested
from incant.stages.ingest.url import url as url_mod
from incant.stages.ingest.url.url import Part, Series, UrlIngest, part_title

INGEST = {s.name: s for s in discover()}["ingest"]


def test_parents_and_settings_inherit(tmp_path):
    mobi = load_method_class(INGEST, "mobi")
    epub = load_method_class(INGEST, "epub")
    ebook = load_method_class(INGEST, "ebook")
    assert parent_methods(INGEST, mobi) == ["ebook"]
    assert parent_methods(INGEST, epub) == ["ebook"]
    assert parent_methods(INGEST, ebook) == []
    assert parent_methods(INGEST, load_method_class(INGEST, "gutenberg")) == ["txt"]
    assert parent_methods(INGEST, load_method_class(INGEST, "url")) == []
    conf = tmp_path / "incant.toml"
    conf.write_text('[ingest.ebook]\nconverter = "pandoc"\ntimeout_s = 12\n[ingest.mobi]\ntimeout_s = 20\n')
    m = instantiate(load_config(conf), INGEST, "mobi")
    assert m.settings.converter == "pandoc"  # from [ingest.ebook]
    assert m.settings.timeout_s == 20  # its own table wins
    assert instantiate(load_config(conf), INGEST, "ebook").settings.timeout_s == 12
    conf.write_text('[ingest.mobi]\ntimeuot_s = 3\n')  # typos in the child's own table are still errors
    with pytest.raises(ValueError):
        instantiate(load_config(conf), INGEST, "mobi")


def test_auto_picks_the_most_specific(tmp_path):
    pg = tmp_path / "pg.txt"
    pg.write_text("Title: X\n\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nBody.\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicense")
    plain = tmp_path / "plain.txt"
    plain.write_text("Title: Y\n\n*** not a marker ***\nBody.\n")
    assert _auto(INGEST, str(pg)) == "gutenberg" and _auto(INGEST, str(plain)) == "txt"
    assert _auto(INGEST, "https://example.com/a") == "url"


def test_gutenberg_cleans_and_txt_does_not(tmp_path):
    pg = tmp_path / "pg.txt"
    pg.write_text("Title: X\nAuthor: Y\n\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n"
                  "[Illustration: A man\n\nChapter I.]\n\nBody text.\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicense")
    cfg = load_config(tmp_path / "none.toml")
    g = instantiate(cfg, INGEST, "gutenberg").load(str(pg))
    t = instantiate(cfg, INGEST, "txt").load(str(pg))
    assert g.paragraphs == ["Chapter I.", "Body text."] and (g.title, g.author) == ("X", "Y")
    assert any("license" in p for p in t.paragraphs)  # plain txt keeps everything


def test_list_nests_children(capsys):
    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.split()[:1] == ["txt"] or " txt " in ln[:20])
    assert "└─ gutenberg" in lines[i + 1]
    assert any("└─ mobi" in ln for ln in lines)
    assert any("└─ epub" in ln for ln in lines)


# ------------------------------------------------------------ series via the url hook


class TwoParts(UrlIngest):
    def series(self, page, fetcher):
        return Series(url="https://site.test/folder/1", title="Saga", author="Ann",
                      parts=[Part("https://site.test/s/1", "Saga"), Part("https://site.test/s/2", "Saga: The Return"),
                             Part("https://site.test/s/3", "Saga: Lost")], skipped=["'Saga: Bios' (matches exclude)"])


def test_series_parts_become_chapters(monkeypatch, tmp_path):
    def fake(url, fetcher=None):
        if url.endswith("/3"):
            raise url_mod.FetchError("gone")
        return url_mod.Page(url, "", Ingested(title="t", paragraphs=[f"Text of {url[-1]}."], source_format="url",
                                              warnings=["URL ingest reads a single page only."]))

    monkeypatch.setattr(url_mod, "fetch_page", fake)
    m = TwoParts(url_mod.UrlSettings(render="never"), load_config(tmp_path / "none.toml"))
    ing = m.load("https://site.test/s/2")
    assert ing.paragraphs == ["# Saga", "Text of 1.", "# The Return", "Text of 2."]
    assert ing.source == "https://site.test/folder/1" and ing.title == "Saga" and ing.author == "Ann"
    assert any("not ingested" in w for w in ing.warnings) and any("Bios" in w for w in ing.warnings)
    assert part_title("Saga", "Saga") == "Saga"
