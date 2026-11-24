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
    # PRE stages (preingest) are not part of the per-book pipeline chain
    chain_line = [ln for ln in out.splitlines() if "A chain with known permissive terms exists" in ln][0]
    assert "preingest=" not in chain_line
    assert "ingest=auto" in chain_line
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


def test_voice_without_consent_is_a_consent_gap_but_keeps_its_terms():
    from incant.credits import consent_gap, text
    from incant.schemas import Voice

    vz = Voice(id="a_janelle_risa", source="voice-zero", license="public domain (Voice-Zero)")
    assert consent_gap(vz, None) == "not recorded (voice provenance: voice-zero)"
    assert consent_gap(vz.model_copy(update={"consent": True}), None) == ""
    assert consent_gap(vz, "consent: recorded for this project") == ""
    assert consent_gap(vz.model_copy(update={"consent": False}), None).startswith("recorded as not given")
    assert consent_gap(None, None) == "not recorded (voice provenance: unknown)"

    item = Item("voice a_janelle_risa", vz.license, vz.license, "source voice-zero", consent=consent_gap(vz, None))
    assert item.kind == "none"  # the recording's terms are unchanged
    rep = Report([Item("synthesize: x", None, "none"), item])
    assert rep.gaps == [] and rep.weakest == "none" and rep.consent_gaps == [item]
    out = text(rep, "Book")
    assert "      consent: not recorded (voice provenance: voice-zero)" in out
    assert "Speaker consent not recorded" in out and "  - voice a_janelle_risa: not recorded" in out
    assert "Gaps (terms" not in out
    assert "Speaker consent" not in text(Report([Item("synthesize: x", None, "none")]), "Book")


def test_method_licenses_name_in_process_copyleft():
    from incant.stages.ingest.mobi.mobi import MobiIngest
    from incant.stages.synthesize.kokoro.kokoro import KokoroTTS

    assert "GPL-3.0 library (in-process)" in MobiIngest.LICENSE and "a tool" not in MobiIngest.LICENSE
    for name in ("espeak-ng", "phonemizer", "misaki", "spaCy"):
        assert name in KokoroTTS.LICENSE
    assert kind(KokoroTTS.OUTPUT_TERMS) == "none" and kind(MobiIngest.OUTPUT_TERMS) == "none"


def test_third_party_notices_and_license_texts_agree():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text()
    shipped = {p.name for p in (root / "LICENSES").iterdir()}
    named = set(re.findall(r"LICENSES/([\w.-]+\.txt)", notices))
    assert named <= shipped, f"named but missing: {named - shipped}"
    assert shipped <= named, f"in LICENSES/ but not referenced: {shipped - named}"
    for needle in ("/usr/lib/incant/venv/lib/python3.13/site-packages", "/usr/share/doc/incant/LICENSES",
                   "libx264", "Written offer", "github.com/BGWJJILLIGKKK/incant"):
        assert needle in notices
    copyright = (root / "packaging" / "debian" / "copyright").read_text()
    assert copyright.startswith("Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/")
    assert "License: BSD-3-Clause" in copyright and "Copyright: 2026 BGWJJILLIGKKK" in copyright
