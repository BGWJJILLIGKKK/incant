"""Kokoro-82M, locally on the CPU (kokoro-onnx). No server, no GPU; roughly 3-5x faster than real time
on a 16-core desktop CPU (the int8 model is smaller, not faster).

    uv sync --extra kokoro          # model files (~340 MB, or ~120 MB for int8) download on first use

Voices: Kokoro's 54 built-in voices (see voicesets.py; English ones are tagged for casting), plus
voices/kokoro.json to retag them or define blends:
    {"narrator_warm": {"mix": {"af_heart": 0.7, "af_bella": 0.3}, "gender": "female", "age": "adult"}}
Each voice speaks in its own accent: a* American, b* British (from the id), or the voice's `lang`.
No emotion control: emotions are ignored.
"""

from __future__ import annotations

import io
import os
import threading
import wave
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field

from ....schemas import Emotion, Voice
from ....voices import VoiceLibrary
from ....voicesets import KOKORO, KOKORO_LANG, KOKORO_NARRATOR
from ..base import SynthesizeMethod, SynthSettings
from . import heteronyms

__all__ = ["KokoroTTS"]

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = {"fp32": "kokoro-v1.0.onnx", "int8": "kokoro-v1.0.int8.onnx"}
VOICES_FILE = "voices-v1.0.bin"
PROTOCOL = 1  # bump when synthesis changes for the same settings (invalidates the segment cache)


class KokoroSettings(SynthSettings):
    model: Literal["fp32", "int8"] = "fp32"
    model_dir: Path = Field(default_factory=lambda: Path(os.environ.get("INCANT_KOKORO_DIR", "~/.cache/incant/kokoro")))
    download: bool = True  # fetch missing model files from the kokoro-onnx release
    speed: float = 1.0
    threads: int = 0  # onnxruntime threads per segment; 0 = all cores
    concurrency: int = 1  # one model; it already uses every core
    max_segment_chars: int = 400
    # Pronunciation for the English voices: "espeak" (kokoro-onnx's built-in phonemizer) or "misaki" (Kokoro's
    # own dictionary-based G2P with spaCy part-of-speech for heteronyms, espeak for unknown words; extra: nlp).
    # "auto": misaki when the nlp extra is installed (it is by default), else espeak.
    g2p: Literal["auto", "espeak", "misaki"] = "auto"


class KokoroTTS(SynthesizeMethod):
    LICENSE = 'Kokoro-82M: Apache-2.0 (code, weights and voices); kokoro-onnx (MIT)'
    OUTPUT_TERMS = 'none'
    Settings = KokoroSettings
    settings: KokoroSettings
    DEFAULT_NARRATOR = KOKORO_NARRATOR

    _lock = threading.Lock()
    _models: dict[tuple, object] = {}  # one loaded model per process, shared by every instance

    def voices(self) -> VoiceLibrary:
        return VoiceLibrary(self.cfg.paths.voices_dir, index="kokoro.json", builtin=KOKORO)

    def resource(self) -> str | None:
        return "kokoro"  # in this process, on the CPU

    def identity(self) -> str:
        s = self.settings
        mode = self.g2p_mode()  # espeak keeps the original key, so espeak audio stays cached
        g2p = f":g2p={mode}-h1" if mode != "espeak" else ""  # h1: heteronym rules (bump when they change)
        return f"kokoro-onnx:v1.0:{s.model}:speed={s.speed}{g2p}:p{PROTOCOL}"

    _g2ps: dict[bool, object] = {}

    def g2p_mode(self) -> str:
        if self.settings.g2p != "auto":
            return self.settings.g2p
        import importlib.util

        return "misaki" if importlib.util.find_spec("misaki") and importlib.util.find_spec("spacy") else "espeak"

    def misaki(self, british: bool):
        """Misaki's English G2P (one per accent, shared), with espeak for words not in its dictionary."""
        with self._lock:
            if british not in self._g2ps:
                try:
                    from misaki import en, espeak
                except ImportError:
                    raise RuntimeError('g2p = "misaki" needs the nlp extra: uv sync --extra nlp') from None
                self._g2ps[british] = en.G2P(trf=False, british=british, fallback=espeak.EspeakFallback(british=british))
            return self._g2ps[british]

    # ------------------------------------------------------------ model files

    def paths(self) -> tuple[Path, Path]:
        d = self.settings.model_dir.expanduser()
        return d / FILES[self.settings.model], d / VOICES_FILE

    def _download(self, path: Path) -> None:
        if not self.settings.download:
            raise RuntimeError(f"Kokoro model file missing: {path} (download it from {RELEASE}/{path.name}, "
                               "or set download = true)")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        with httpx.stream("GET", f"{RELEASE}/{path.name}", follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for block in r.iter_bytes(1 << 20):
                    f.write(block)
        tmp.replace(path)

    def model(self):
        model_path, voices_path = self.paths()
        key = (str(model_path), self.settings.threads)
        with self._lock:
            if key not in self._models:
                try:
                    import onnxruntime as ort
                    from kokoro_onnx import Kokoro
                except ImportError:
                    raise RuntimeError("Kokoro needs kokoro-onnx: uv sync --extra kokoro") from None
                for p in (model_path, voices_path):
                    if not p.exists():
                        self._download(p)
                opts = ort.SessionOptions()
                if self.settings.threads:
                    opts.intra_op_num_threads = self.settings.threads
                sess = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
                self._models[key] = Kokoro.from_session(sess, str(voices_path))
            return self._models[key]

    # ------------------------------------------------------------ synthesis

    def _style(self, kokoro, voice: Voice):
        if not voice.mix:
            return voice.id
        total = sum(voice.mix.values()) or 1.0
        return sum(kokoro.get_voice_style(v) * (w / total) for v, w in voice.mix.items())

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None, variant: int = 0) -> bytes:
        import numpy as np

        if voice is None:
            raise ValueError("Kokoro needs a voice: set narrator under [synthesize.kokoro] or edit cast.json")
        kokoro = self.model()
        base = next(iter(voice.mix)) if voice.mix else voice.id
        lang = voice.lang or KOKORO_LANG.get(base[:1], "en-us")
        speed = self.settings.speed * (1.0, 0.96, 1.04)[variant % 3]  # deterministic model: retry = other tempo
        style = self._style(kokoro, voice)
        if self.g2p_mode() == "misaki" and lang in ("en-us", "en-gb"):
            g2p = self.misaki(british=lang == "en-gb")
            with self._lock:  # spaCy and espeak aren't thread-safe
                _, tokens = g2p(text)
                heteronyms.fix(tokens)  # bow/bowed by context
                phonemes = heteronyms.phonemes(tokens).replace("❓", "").strip()
                audio, rate = kokoro.create(phonemes, voice=style, speed=speed, lang=lang, is_phonemes=True)
        else:
            with self._lock:  # the phonemizer (espeak) isn't thread-safe
                audio, rate = kokoro.create(text, voice=style, speed=speed, lang=lang)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()

    def check(self) -> list[tuple[bool, str]]:
        out = []
        try:
            import kokoro_onnx  # noqa: F401
            out.append((True, "kokoro-onnx installed"))
        except ImportError:
            out.append((False, "kokoro-onnx not installed: uv sync --extra kokoro"))
        out.append((True, f"pronunciation: {self.g2p_mode()}" + (" (install the nlp extra for misaki)"
                    if self.g2p_mode() == "espeak" and self.settings.g2p == "auto" else "")))
        for p in self.paths():
            out.append((p.exists() or self.settings.download,
                        f"{p}" + ("" if p.exists() else " (downloads on first use)" if self.settings.download else " missing")))
        lib = self.voices().voices
        bad = [f"{v.id}: {m}" for v in lib.values() for m in (v.mix or {}) if m not in KOKORO]
        if bad:
            out.append((False, "blends name unknown Kokoro voices: " + ", ".join(bad)))
        return out + super().check()
