"""IndexTTS-2.5 over HTTP (OpenAI-style /v1/audio/speech, named server-side voices, free-text emotion).

Request shape:
    {"model", "input", "voice", "response_format": "wav", "speed",
     "extra_params": {"lang", "use_random", "use_emo_text", "emo_text", "emo_alpha"}}

The server handles one request at a time, so concurrency above ~2 only queues
(2 overlaps network/IO with generation). The first request after llama-swap swaps
the model in takes ~30 s; the timeout allows for it.
"""

from __future__ import annotations

import time

import httpx

from ....schemas import Emotion, Voice
from ..base import SynthesizeMethod, SynthSettings

__all__ = ["IndexTTS"]

PROTOCOL = 2  # bump when the request shape changes (invalidates the segment cache)


class IndexTTSSettings(SynthSettings):
    concurrency: int = 2
    base_url: str = "http://localhost:8080/v1"
    model: str = "indextts-2.5"
    lang: str = "en"
    speed: float = 1.0
    use_random: bool = False  # deterministic sampling -> reproducible, cacheable output
    timeout_s: float = 300.0
    retries: int = 3


class IndexTTS(SynthesizeMethod):
    Settings = IndexTTSSettings
    settings: IndexTTSSettings

    def __init__(self, settings, cfg):
        super().__init__(settings, cfg)
        self.http = httpx.Client(base_url=self.settings.base_url.rstrip("/"), timeout=self.settings.timeout_s)

    def identity(self) -> str:
        s = self.settings
        return f"indextts-http:{s.model}:{s.lang}:speed={s.speed}:random={s.use_random}:p{PROTOCOL}"

    def payload(self, text: str, voice: Voice, emotion: Emotion | None) -> dict:
        s = self.settings
        extra: dict = {"lang": s.lang, "use_random": s.use_random}
        if emotion:
            extra |= {"use_emo_text": True, "emo_text": emotion.text, "emo_alpha": emotion.alpha}
        body = {"model": s.model, "input": text, "voice": voice.id, "response_format": "wav", "extra_params": extra}
        if s.speed != 1.0:
            body["speed"] = s.speed
        return body

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None, variant: int = 0) -> bytes:
        if voice is None:
            raise ValueError("IndexTTS needs a voice: set [cast] narrator in incant.toml or edit the book's cast.json")
        body = self.payload(text, voice, emotion)
        if variant:  # QA retry: ask for a different take
            body["extra_params"]["use_random"] = True
        last: str = ""
        for attempt in range(self.settings.retries + 1):
            try:
                r = self.http.post("/audio/speech", json=body)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    return r.content
                detail = r.text[:400]
                if r.status_code < 500:  # bad request: retrying won't help
                    raise RuntimeError(f"TTS rejected segment ({r.status_code}): {detail}\n  text: {text[:120]!r}")
                last = f"HTTP {r.status_code}: {detail}"
            time.sleep(2**attempt)
        raise RuntimeError(f"TTS failed after {self.settings.retries + 1} attempts: {last}")

    def check(self) -> list[tuple[bool, str]]:
        out = []
        try:  # /models doesn't load anything, so this won't trigger a llama-swap swap
            ids = [m["id"] for m in self.http.get("/models", timeout=10).json().get("data", [])]
            ok = self.settings.model in ids
            out.append((ok, f"model '{self.settings.model}' at {self.settings.base_url}" + ("" if ok else f" (have: {', '.join(ids)})")))
        except Exception as e:  # noqa: BLE001
            out.append((False, f"endpoint {self.settings.base_url}: {e}"))
        lib = self.voices()
        out.append((bool(lib.voices), f"{len(lib.voices)} voices in {lib.root}"))
        narr = self.cfg.cast.narrator
        known = bool(narr and narr in lib.voices)
        out.append((known, f"[cast] narrator = {narr!r}" + ("" if known else " (unset or not in voice library)")))
        return out
