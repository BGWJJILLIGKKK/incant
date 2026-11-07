"""Offline stand-in TTS: one soft beep per word, pitch keyed to the voice.

Lets the whole pipeline run (and be tested) without a GPU. Timing is roughly
speech-like (~2.7 words/s) so chapter markers land in plausible places, and
speaker changes are audible as pitch changes.
"""

from __future__ import annotations

import hashlib
import io
import math
import wave
from array import array

from ....schemas import Emotion, Voice
from ..base import SynthesizeMethod

__all__ = ["DummyTTS"]

RATE = 24000


class DummyTTS(SynthesizeMethod):
    NEEDS_VOICE_LIBRARY = False
    QA_SUPPORTED = False

    def __init__(self, settings, cfg):
        super().__init__(settings, cfg)
        self._words: dict[tuple[int, float], bytes] = {}

    def identity(self) -> str:
        return "dummy-v1"

    def _word(self, freq: int, amp: float) -> bytes:
        if (freq, amp) not in self._words:
            n, gap = int(RATE * 0.26), int(RATE * 0.11)
            a = array("h", (int(amp * math.sin(math.pi * i / n) * math.sin(2 * math.pi * freq * i / RATE)) for i in range(n)))
            a.extend([0] * gap)
            self._words[(freq, amp)] = a.tobytes()
        return self._words[(freq, amp)]

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None, variant: int = 0) -> bytes:
        key = voice.id if voice else "default"
        freq = 180 + int(hashlib.sha1(key.encode()).hexdigest(), 16) % 300
        amp = 2500 * (1.0 + (emotion.alpha if emotion else 0.0))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(self._word(freq, amp) * len(text.split()))
        return buf.getvalue()
