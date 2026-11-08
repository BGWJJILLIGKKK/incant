"""Kokoro with Misaki pronunciation (nlp extra): phonemes go to the model; espeak keeps the old cache key."""

import io
import wave
from pathlib import Path

import pytest

from incant.config import load_config
from incant.pipeline import discover, instantiate

pytest.importorskip("misaki")
pytest.importorskip("kokoro_onnx")
MODEL = Path("~/.cache/incant/kokoro/kokoro-v1.0.onnx").expanduser()


def kokoro(tmp_path, g2p):
    conf = tmp_path / "incant.toml"
    conf.write_text(f'[synthesize.kokoro]\ng2p = "{g2p}"\n')
    return instantiate(load_config(conf), {s.name: s for s in discover()}["synthesize"], "kokoro")


def test_identity_only_changes_for_misaki(tmp_path):
    assert ":g2p=" not in kokoro(tmp_path, "espeak").identity()
    assert ":g2p=misaki" in kokoro(tmp_path, "misaki").identity()


def test_misaki_phonemes_with_espeak_fallback(tmp_path):
    g = kokoro(tmp_path, "misaki").misaki(british=False)
    ps = g("Mr. Darcy bowed to the Fezziwigs.")[0]
    assert "❓" not in ps and "dˈɑɹsi" in ps  # an out-of-dictionary name, via espeak


@pytest.mark.skipif(not MODEL.exists(), reason="Kokoro model files not downloaded")
def test_misaki_synthesizes(tmp_path):
    m = kokoro(tmp_path, "misaki")
    wav = m.synthesize("The wind was too strong to wind the sail.", m.voices().get("bf_emma"), None)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() / w.getframerate() > 1.5


def test_bow_heteronym_rules():
    from incant.stages.synthesize.kokoro import heteronyms

    g = kokoro(Path("/tmp"), "misaki").misaki(british=False)

    def bows(text):
        _, toks = g(text)
        heteronyms.fix(toks)
        return [t.phonemes for t in toks if heteronyms.BOW.match(t.text)]

    assert bows("Mr. Darcy bowed to Elizabeth.") == ["bˈWd"]  # bent at the waist (rhymes with "now")
    assert bows("He bowed low and made a deep bow.") == ["bˈWd", "bˈW"]
    assert bows("She bowed the cello and tied a bow in her hair.") == ["bˈOd", "bˈO"]  # rhymes with "go"
    assert bows("The bows of the ships rose.") == ["bˈWz"] and bows("He drew his bow and an arrow.") == ["bˈO"]


def test_auto_picks_misaki_when_installed(tmp_path):
    m = kokoro(tmp_path, "auto")
    assert m.g2p_mode() == "misaki" and ":g2p=misaki-h1" in m.identity()
