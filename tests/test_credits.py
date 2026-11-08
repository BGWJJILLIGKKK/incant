"""Credits and license report: known terms, user declarations, gaps, and the --licenses chain."""

import shutil

import pytest

from incant.cli import main
from incant.config import load_config
from incant.credits import Item, Report, kind, licenses_table
from incant.pipeline import discover
from test_pipeline import make_epub

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def test_kinds_and_weakest():
    assert kind("none") == "none" and kind("Apache-2.0") == "none" and kind(None) is None
    assert kind("attribution: credit LibriTTS-R (CC-BY 4.0)") == "attribution"
    assert kind("non-commercial: IndexTTS checkpoint") == "non-commercial"
    assert kind("provider terms: OpenAI") == "provider terms" and kind("something odd") is None
    rep = Report([Item("a", None, "none"), Item("b", None, "attribution: x"), Item("c", None, None)])
    assert rep.weakest == "attribution" and [i.what for i in rep.gaps] == ["c"]


def test_licenses_table_reports_chain_and_gaps(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text('[pipeline]\ningest = "auto"\nsynthesize = "kokoro"\n')
    out = licenses_table(load_config(conf), discover())
    assert "A chain with known permissive terms exists" in out and "synthesize=kokoro" in out
    assert "indextts    unknown" in out
    conf.write_text('[credits]\n"synthesize.indextts" = "non-commercial: research checkpoint"\nllm = "provider terms: x"\n')
    out = licenses_table(load_config(conf), discover())
    assert "indextts    non-commercial" in out and "LLM qwen3.5-9b: provider terms: x" in out


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_credits_txt_beside_the_audiobook(tmp_path):
    src = tmp_path / "t.epub"
    make_epub(src)
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\noutput_dir = "library"\n[pipeline]\nparse = "basic"\n'
                    'attribute = "narrator"\nnormalize = "rules"\nsynthesize = "dummy"\n'
                    '[credits]\nsource = "none: public domain"\n')
    assert main([str(src), "-c", str(conf)]) == 0
    credits = (tmp_path / "library" / "A. Writer" / "Test Book" / "credits.txt").read_text()
    assert "synthesize: dummy" in credits and "source text" in credits and "Weakest known terms: none" in credits
    assert "Gaps" not in credits  # everything in this chain is known
    rec = (tmp_path / "work" / "test-book" / "output.json").read_text()
    assert "credits.txt" in rec
