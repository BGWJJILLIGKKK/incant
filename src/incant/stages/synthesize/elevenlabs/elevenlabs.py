"""ElevenLabs (cloud, paid): POST /v1/text-to-speech/{voice_id}, raw 24 kHz PCM back.

    [synthesize.elevenlabs]
    api_key_env = "ELEVENLABS_API_KEY"     # environment or the data directory's .env
    model_id = "eleven_multilingual_v2"
    # narrator = "<voice id>"; allow_over_quota = false

Voices: your account's voices (GET /v1/voices), their gender/age labels used for automatic casting, plus
voices/elevenlabs.json to retag or add ids. Emotion lowers `stability` (more expressive delivery); QA retries
use another `seed`. Before rendering, the characters still to synthesize are checked against the
subscription's remaining credits: the run stops with the numbers unless allow_over_quota = true.
Free-tier audio must credit ElevenLabs (see credits.txt).
"""

from __future__ import annotations

import re

import httpx

from ....audio import pcm_to_wav
from ....schemas import Emotion, Voice
from ....voices import VoiceLibrary
from ..speech_http import SpeechHTTP, SpeechHTTPSettings

__all__ = ["ElevenLabs"]

RATE = 24000
PROTOCOL = 1  # bump when the request shape changes (invalidates the segment cache)
# ElevenLabs' "young" is a young adult (Sarah, "young", is described as "Mature"): "neutral" ranks between teen and
# adult in casting.
AGE = {"young": "neutral", "middle_aged": "adult", "middle aged": "adult", "old": "older"}
# Stock voices that synthesized with a free-tier key (2026-11); others answered 402 paid_plan_required or 404.
# Keys without voices_read can still use these; account voices and voices/elevenlabs.json add to them.
STOCK = {vid: Voice(id=vid, name=name, gender=g, age="adult", tone=tone, description=desc, source="elevenlabs (stock)")
         for vid, name, g, tone, desc in [
             ("JBFqnCBsd6RMkjVDRZzb", "George", "male", "neutral", "British, warm storyteller, narration"),
             ("onwK4e9ZLuTAKqWW03F9", "Daniel", "male", "deep", "British, authoritative"),
             ("nPczCjzI2devNBz1zQrb", "Brian", "male", "deep", "American, deep, narration"),
             ("EXAVITQu4vr4xnSDxMaL", "Sarah", "female", "neutral", "American, soft"),
             ("pFZP5JQG7iQjIQuC4Bku", "Lily", "female", "neutral", "British, warm"),
             ("Xb7hH8MSUJpSbSDYk0k2", "Alice", "female", "neutral", "British, confident"),
         ]}


def missing_permission(r: httpx.Response) -> str | None:
    """'voices_read' etc. when a scoped API key lacks a permission."""
    try:
        detail = r.json().get("detail", {})
    except ValueError:
        return None
    m = re.search(r"missing the permission (\w+)", str(detail.get("message", "")))
    return m.group(1) if r.status_code == 401 and m else None


class ElevenLabsSettings(SpeechHTTPSettings):
    base_url: str = "https://api.elevenlabs.io"
    api_key_env: str | None = "ELEVENLABS_API_KEY"
    model_id: str = "eleven_multilingual_v2"
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    seed: int = 1234  # best-effort determinism (cache-friendly); a QA retry uses seed + n
    allow_over_quota: bool = False
    concurrency: int = 2
    max_segment_chars: int = 400


class ElevenLabs(SpeechHTTP):
    Settings = ElevenLabsSettings
    settings: ElevenLabsSettings
    LICENSE = "ElevenLabs API (proprietary service)"
    OUTPUT_TERMS = "provider terms: ElevenLabs Terms of Service; the free tier requires crediting ElevenLabs"

    def auth_headers(self, key: str) -> dict[str, str]:
        return {"xi-api-key": key}

    def identity(self) -> str:
        s = self.settings
        return (f"elevenlabs:{s.model_id}:pcm_{RATE}:stab={s.stability}:sim={s.similarity_boost}:style={s.style}"
                f":speed={s.speed}:seed={s.seed}:p{PROTOCOL}")

    # ------------------------------------------------------------ voices

    def account_voices(self) -> dict[str, Voice]:
        if not hasattr(self, "_account"):
            self._account: dict[str, Voice] = {}
            self.voices_note = ""
            if "xi-api-key" in self.http.headers:
                try:
                    r = self.http.get("/v1/voices", timeout=30)
                    if perm := missing_permission(r):
                        self.voices_note = f"the API key lacks {perm}: stock voices and voices/elevenlabs.json only"
                        return self._account
                    r.raise_for_status()
                    for v in r.json().get("voices", []):
                        labels = {k: str(val).lower() for k, val in (v.get("labels") or {}).items()}
                        gender = labels.get("gender") if labels.get("gender") in ("male", "female") else None
                        desc = labels.get("description") or labels.get("descriptive") or ""
                        cue = f"{v.get('name', '')} {desc}".lower()  # names carry it too: "Brian - Deep, Resonant"
                        self._account[v["voice_id"]] = Voice(
                            id=v["voice_id"], name=v.get("name"), gender=gender,
                            age=AGE.get(labels.get("age", ""), "adult" if gender else None),
                            tone="deep" if re.search(r"deep|husky|gravel", cue) else
                                 ("high" if re.search(r"high|bright|squeak", cue) else "neutral"),
                            description=", ".join(x for x in (labels.get("accent"), desc, labels.get("use_case")) if x),
                            source=f"elevenlabs ({v.get('category', 'voice')})")
                except (httpx.HTTPError, ValueError, KeyError):
                    self._account = {}
        return self._account

    def voices(self) -> VoiceLibrary:
        return VoiceLibrary(self.cfg.paths.voices_dir, index="elevenlabs.json",
                            builtin=STOCK | self.account_voices())

    @property
    def DEFAULT_NARRATOR(self) -> str | None:  # type: ignore[override]
        voices = list(self.account_voices().values())
        narr = [v for v in voices if "narrat" in (v.description or "") or "audiobook" in (v.description or "")]
        pool = narr or voices
        return pool[0].id if pool else "JBFqnCBsd6RMkjVDRZzb"  # George

    # ------------------------------------------------------------ synthesis

    def request(self, text: str, voice: Voice, emotion: Emotion | None, variant: int) -> tuple[str, dict]:
        s = self.settings
        stability = s.stability * (1 - 0.5 * emotion.alpha) if emotion else s.stability
        body = {"text": text, "model_id": s.model_id, "seed": s.seed + variant,
                "voice_settings": {"stability": round(stability, 3), "similarity_boost": s.similarity_boost,
                                   "style": s.style, "speed": s.speed, "use_speaker_boost": True}}
        return f"/v1/text-to-speech/{voice.id}?output_format=pcm_{RATE}", body

    def decode(self, response: httpx.Response) -> bytes:
        return pcm_to_wav(response.content, RATE)

    # ------------------------------------------------------------ cost guard

    def subscription(self) -> dict | None:
        """The subscription, or None when the key may not read it (no user_read permission)."""
        r = self.http.get("/v1/user/subscription", timeout=30)
        if missing_permission(r):
            return None
        r.raise_for_status()
        return r.json()

    def before_render(self, renderer, ctx) -> None:
        chars, segs = renderer.pending_chars(ctx.chapters)
        if not chars:
            return
        sub = self.subscription()
        if sub is None:
            msg = (f"ElevenLabs: {chars:,} characters to synthesize ({segs} segments), but this API key may not read "
                   "the subscription (user_read), so the remaining credits can't be checked")
            if not self.settings.allow_over_quota:
                raise RuntimeError(msg + "; grant the key user_read, or set allow_over_quota = true to go ahead")
            ctx.log(f"  {msg}; going ahead (allow_over_quota = true)")
            return
        left = int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))
        ctx.log(f"  ElevenLabs: {chars:,} characters to synthesize ({segs} segments); "
                f"{left:,} left this period ({sub.get('tier', '?')} tier)")
        if chars > left and not self.settings.allow_over_quota:
            raise RuntimeError(f"ElevenLabs: this needs {chars:,} characters but the subscription has {left:,} left; "
                               "render fewer chapters (--chapters), or set allow_over_quota = true")

    def check(self) -> list[tuple[bool, str]]:
        out = []
        if "xi-api-key" not in self.http.headers:
            return [(False, f"no API key: set {self.settings.api_key_env} in the environment or the data directory's .env")]
        try:
            sub = self.subscription()
            if sub is None:
                out.append((True, "the key may not read the subscription (user_read): credits can't be checked before "
                                  "rendering; grant user_read or set allow_over_quota = true"))
            else:
                left = int(sub.get("character_limit", 0)) - int(sub.get("character_count", 0))
                out.append((True, f"{sub.get('tier', '?')} tier, {left:,} characters left this period"))
        except httpx.HTTPError as e:
            out.append((False, f"subscription: {e}"))
        lib = self.voices()
        out.append((bool(lib.voices), f"{len(lib.voices)} voices ({lib.describe()})"
                    + (f"; {self.voices_note}" if getattr(self, "voices_note", "") else "")))
        narr = self.narrator()
        out.append((narr is not None, f"narrator voice: {narr!r}"))
        return out
