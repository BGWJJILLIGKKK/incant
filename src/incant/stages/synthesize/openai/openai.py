"""Any OpenAI-compatible speech server (POST /v1/audio/speech): Kokoro-FastAPI, openedai-speech,
LocalAI, OpenAI itself.

    [synthesize.openai]
    base_url = "http://localhost:8880/v1"   # Kokoro-FastAPI's default
    model = "kokoro"
    voices = "kokoro"        # built-in voice list: "kokoro", "openai", or "none" (only voices/openai.json)
    # api_key_env = "OPENAI_API_KEY"; instructions = true for gpt-4o-mini-tts emotion

Voices come from the built-in list named by `voices`, plus voices/openai.json ({id: {gender, age, tone, mix…}}).
A voice with `mix` is sent as a Kokoro-FastAPI blend: "af_heart(0.7)+af_bella(0.3)".
"""

from __future__ import annotations

from typing import Literal

from ....schemas import Emotion, Voice
from ....voices import VoiceLibrary
from ....voicesets import BUILTIN
from ..speech_http import SpeechHTTP, SpeechHTTPSettings

__all__ = ["OpenAISpeech"]

PROTOCOL = 1  # bump when the request shape changes (invalidates the segment cache)


class OpenAISpeechSettings(SpeechHTTPSettings):
    voices: Literal["kokoro", "openai", "none"] = "kokoro"
    response_format: str = "wav"
    instructions: bool = False  # send each line's emotion as `instructions` (gpt-4o-mini-tts)
    concurrency: int = 2


class OpenAISpeech(SpeechHTTP):
    Settings = OpenAISpeechSettings
    settings: OpenAISpeechSettings

    def voices(self) -> VoiceLibrary:
        builtin, _ = BUILTIN[self.settings.voices]
        return VoiceLibrary(self.cfg.paths.voices_dir, index="openai.json", builtin=builtin)

    @property
    def DEFAULT_NARRATOR(self) -> str | None:  # type: ignore[override]
        return BUILTIN[self.settings.voices][1]

    def voice_space(self) -> str:
        s = self.settings
        return f"openai-{s.voices}" if s.voices != "none" else "openai"

    def identity(self) -> str:
        s = self.settings
        return f"openai-speech:{s.base_url}:{s.model}:speed={s.speed}:instr={s.instructions}:p{PROTOCOL}"

    def payload(self, text: str, voice: Voice, emotion: Emotion | None, variant: int) -> dict:
        s = self.settings
        name = "+".join(f"{k}({w:g})" for k, w in voice.mix.items()) if voice.mix else voice.id
        speed = s.speed * (1.0, 0.96, 1.04)[variant % 3]  # QA retry: a slightly different take
        body = {"model": s.model, "input": text, "voice": name, "response_format": s.response_format}
        if speed != 1.0:
            body["speed"] = round(speed, 3)
        if s.instructions and emotion:
            body["instructions"] = f"Speak in a {emotion.text} way."
        return body
