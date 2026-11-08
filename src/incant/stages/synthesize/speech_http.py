"""Shared client for OpenAI-style /v1/audio/speech servers: retries, error reporting, endpoint check."""

from __future__ import annotations

import time

import httpx

from ...config import secret
from ...schemas import Emotion, Voice
from .base import SynthesizeMethod, SynthSettings


class SpeechHTTPSettings(SynthSettings):
    base_url: str = "http://localhost:8880/v1"
    model: str = "kokoro"
    api_key: str | None = None  # prefer api_key_env (and keep keys in incant.local.toml)
    api_key_env: str | None = None
    speed: float = 1.0
    timeout_s: float = 300.0
    retries: int = 3


class SpeechHTTP(SynthesizeMethod):
    """Base for HTTP backends: subclasses build the request body (payload) and name their identity."""

    settings: SpeechHTTPSettings

    def __init__(self, settings, cfg):
        super().__init__(settings, cfg)
        s = self.settings
        key = secret(s.api_key_env) if s.api_key_env else s.api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.http = httpx.Client(base_url=s.base_url.rstrip("/"), timeout=s.timeout_s, headers=headers)

    def payload(self, text: str, voice: Voice, emotion: Emotion | None, variant: int) -> dict:
        raise NotImplementedError

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None, variant: int = 0) -> bytes:
        if voice is None:
            raise ValueError(f"{self.name} needs a voice: set narrator under [synthesize.{self.name}] or [cast], "
                             "or edit the book's cast.json")
        body = self.payload(text, voice, emotion, variant)
        last = ""
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
        s = self.settings
        out = []
        if s.api_key_env and not secret(s.api_key_env):
            out.append((False, f"environment variable {s.api_key_env} is not set"))
        try:  # /models doesn't load anything, so this won't trigger a llama-swap swap
            r = self.http.get("/models", timeout=10)
            ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
            ok = s.model in ids or not ids  # some servers don't list models
            out.append((ok, f"model '{s.model}' at {s.base_url}" + ("" if ok else f" (have: {', '.join(ids)})")))
        except Exception as e:  # noqa: BLE001
            out.append((False, f"endpoint {s.base_url}: {e}"))
        return out + super().check()
