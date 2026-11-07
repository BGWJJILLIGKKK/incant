"""IndexTTS-2.5 over HTTP (OpenAI-style /v1/audio/speech, named server-side voices, free-text emotion).

Request shape:
    {"model", "input", "voice", "response_format": "wav", "speed",
     "extra_params": {"lang", "use_random", "use_emo_text", "emo_text", "emo_alpha"}}

Voices: voices/voices.json (the names the server knows, with casting tags).
The server handles one request at a time, so concurrency above ~2 only queues
(2 overlaps network/IO with generation). The first request after llama-swap swaps
the model in takes ~30 s; the timeout allows for it.
"""

from __future__ import annotations

from ....schemas import Emotion, Voice
from ..speech_http import SpeechHTTP, SpeechHTTPSettings

__all__ = ["IndexTTS"]

PROTOCOL = 2  # bump when the request shape changes (invalidates the segment cache)


class IndexTTSSettings(SpeechHTTPSettings):
    concurrency: int = 2
    base_url: str = "http://localhost:8080/v1"
    model: str = "indextts-2.5"
    lang: str = "en"
    use_random: bool = False  # deterministic sampling -> reproducible, cacheable output


class IndexTTS(SpeechHTTP):
    Settings = IndexTTSSettings
    settings: IndexTTSSettings

    def identity(self) -> str:
        s = self.settings
        return f"indextts-http:{s.model}:{s.lang}:speed={s.speed}:random={s.use_random}:p{PROTOCOL}"

    def payload(self, text: str, voice: Voice, emotion: Emotion | None, variant: int = 0) -> dict:
        s = self.settings
        extra: dict = {"lang": s.lang, "use_random": s.use_random or bool(variant)}  # QA retry: a different take
        if emotion:
            extra |= {"use_emo_text": True, "emo_text": emotion.text, "emo_alpha": emotion.alpha}
        body = {"model": s.model, "input": text, "voice": voice.id, "response_format": "wav", "extra_params": extra}
        if s.speed != 1.0:
            body["speed"] = s.speed
        return body
