"""synthesize=elevenlabs against a mock API: voices, request shape, PCM -> WAV, quota guard."""

import io
import json
import wave
from types import SimpleNamespace

import httpx
import pytest

from incant.config import load_config
from incant.pipeline import discover, instantiate
from incant.schemas import Emotion

VOICES = {"voices": [
    {"voice_id": "v1", "name": "Rachel", "category": "premade",
     "labels": {"gender": "female", "age": "young", "accent": "american", "description": "calm", "use_case": "narration"}},
    {"voice_id": "v2", "name": "Adam", "category": "premade",
     "labels": {"gender": "male", "age": "middle_aged", "description": "deep"}},
]}


def make(tmp_path, monkeypatch, sub=None, extra=""):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    conf = tmp_path / "incant.toml"
    conf.write_text("[synthesize.elevenlabs]\n" + extra)
    m = instantiate(load_config(conf), {s.name: s for s in discover()}["synthesize"], "elevenlabs")
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path == "/v1/voices":
            return httpx.Response(200, json=VOICES)
        if req.url.path == "/v1/user/subscription":
            return httpx.Response(200, json=sub or {"tier": "starter", "character_count": 100, "character_limit": 1100})
        return httpx.Response(200, content=b"\x01\x00" * 2400)  # 0.1 s of PCM

    m.http = httpx.Client(base_url="https://api.elevenlabs.io", transport=httpx.MockTransport(handler),
                          headers=dict(m.http.headers))
    return m, seen


def test_voices_and_request(tmp_path, monkeypatch):
    m, seen = make(tmp_path, monkeypatch)
    lib = m.voices().voices
    assert lib["v1"].gender == "female" and lib["v1"].age == "teen" and lib["v2"].tone == "deep"
    assert m.narrator() == "v1"  # the narration voice
    wav = m.synthesize("Hello there.", lib["v2"], Emotion(text="angry", alpha=0.8), variant=2)
    req = seen[-1]
    body = json.loads(req.content)
    assert req.url.path == "/v1/text-to-speech/v2" and req.url.params["output_format"] == "pcm_24000"
    assert req.headers["xi-api-key"] == "xi-test" and "authorization" not in req.headers
    assert body["seed"] == 1236 and body["voice_settings"]["stability"] == 0.3 and body["model_id"].startswith("eleven_")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 2400
    assert "elevenlabs:eleven_multilingual_v2:pcm_24000" in m.identity()


def test_quota_guard(tmp_path, monkeypatch):
    logs = []
    ctx = SimpleNamespace(chapters=None, log=logs.append)
    renderer = SimpleNamespace(pending_chars=lambda only: (1500, 10))
    m, _ = make(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="needs 1,500 characters but the subscription has 1,000 left"):
        m.before_render(renderer, ctx)
    m, _ = make(tmp_path, monkeypatch, extra="allow_over_quota = true\n")
    m.before_render(renderer, ctx)  # allowed
    m.before_render(SimpleNamespace(pending_chars=lambda only: (0, 0)), ctx)  # all cached: no API call needed
    assert any("1,500 characters to synthesize" in line for line in logs)


def test_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    conf = tmp_path / "incant.toml"
    conf.write_text("")
    m = instantiate(load_config(conf), {s.name: s for s in discover()}["synthesize"], "elevenlabs")
    assert m.voices().voices == {} and m.check()[0][0] is False
