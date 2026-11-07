"""Speech-to-text check of rendered segments (faster-whisper on CPU).

TTS models skip, repeat or garble words; transcribing each segment and comparing it with the text it
should say catches that. Comparison is a word error rate over normalised words (case, punctuation,
hyphens and apostrophes ignored). Optional dependency: `uv sync --extra qa`.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"(\d[\d,]*)(st|nd|rd|th)?")


def _spell(m: re.Match) -> str:
    """Whisper writes "10,000" and "3rd"; the text says "ten thousand" and "third"."""
    try:
        from num2words import num2words
    except ImportError:
        return m.group(0)
    n = int(m.group(1).replace(",", ""))
    return " " + num2words(n, to="ordinal" if m.group(2) else "cardinal") + " "


_CONTRACTIONS = {
    "what's": "what is", "who's": "who is", "that's": "that is", "it's": "it is", "there's": "there is",
    "here's": "here is", "he's": "he is", "she's": "she is", "where's": "where is", "how's": "how is",
    "let's": "let us", "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "i've": "i have", "you've": "you have", "we've": "we have", "they've": "they have", "i'll": "i will",
    "you'll": "you will", "he'll": "he will", "she'll": "she will", "we'll": "we will", "they'll": "they will",
    "i'd": "i would", "you'd": "you would", "he'd": "he would", "she'd": "she would", "we'd": "we would",
    "won't": "will not", "can't": "cannot", "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "isn't": "is not", "aren't": "are not", "wasn't": "was not", "weren't": "were not", "shan't": "shall not",
    "couldn't": "could not", "wouldn't": "would not", "shouldn't": "should not", "haven't": "have not",
    "hasn't": "has not", "hadn't": "had not", "ain't": "is not", "thank'ee": "thank you", "'em": "them",
}
_CONTRACTION_RE = re.compile(r"(?<![\w'])(" + "|".join(re.escape(k) for k in sorted(_CONTRACTIONS, key=len, reverse=True)) + r")(?![\w'])")
_INITIALS = re.compile(r"\b((?:[a-z]\.\s*){2,})")


def words(text: str) -> list[str]:
    """Comparable words: whisper expands contractions ("What's" -> "What is"), writes numbers as digits and
    initials without stops ("C. D." -> "CD"); both sides are brought to the same form."""
    t = _NUMBER.sub(_spell, text.casefold()).replace("’", "'")
    t = _CONTRACTION_RE.sub(lambda m: _CONTRACTIONS[m.group(1)], t)
    t = _INITIALS.sub(lambda m: re.sub(r"[.\s]", "", m.group(1)) + " ", t)
    t = t.replace("'", "").replace("-", " ").replace("—", " ")
    return _WORD.findall(t)


def edit_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


@dataclass
class Check:
    wer: float
    edits: int
    heard: str

    def ok(self, max_wer: float, min_edits: int = 2) -> bool:
        # Short lines: one misheard word is whisper noise, not a TTS failure.
        return self.wer <= max_wer or self.edits < min_edits


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


class Transcriber:
    def __init__(self, model: str = "base.en", threads: int = 8):
        self.model_name, self.threads = model, threads
        self._model = None
        self._lock = threading.Lock()

    def _get(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=self.threads)
            return self._model

    @staticmethod
    def _pcm16k(wav: bytes):
        import numpy as np

        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
                             input=wav, capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.float32)

    def check(self, wav: bytes, text: str) -> Check:
        model = self._get()
        segments, _ = model.transcribe(self._pcm16k(wav), language="en", beam_size=1, vad_filter=False,
                                       condition_on_previous_text=False)
        return compare(text, " ".join(s.text.strip() for s in segments))


def compare(text: str, heard: str) -> Check:
    """`text` is what the TTS was given (already normalised); whisper writes "Mr." and "1888", so the
    transcript goes through the same normalisation before comparing."""
    try:
        from .speech import normalize

        heard_n = normalize(heard)
    except Exception:  # noqa: BLE001 — never let QA crash a render
        heard_n = heard
    ref, hyp = words(text), words(heard_n)
    edits = edit_distance(ref, hyp)
    return Check(wer=edits / max(1, len(ref)), edits=edits, heard=heard)
