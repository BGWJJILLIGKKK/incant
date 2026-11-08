"""Secrets from .env files, retries, and the Claude (Anthropic SDK) dialect against a stand-in client."""

import os
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from incant import config as config_mod
from incant import llm as llm_mod
from incant import llm_anthropic
from incant.config import LLMConfig, exposed_env_files, load_config, secret


class Out(BaseModel):
    x: int


def test_secret_lookup_order(tmp_path, monkeypatch):
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir(), data.mkdir()
    (app / "incant.toml").write_text("")
    (app / ".env").write_text("A_KEY=from-app\nB_KEY=app-only\nINCANT_DATA=" + str(data) + "\n")
    (data / ".env").write_text("A_KEY=from-data\n")
    monkeypatch.delenv("INCANT_DATA", raising=False)
    monkeypatch.delenv("A_KEY", raising=False)
    load_config(app / "incant.toml")
    assert secret("A_KEY") == "from-data" and secret("B_KEY") == "app-only" and secret("NOPE") is None
    monkeypatch.setenv("A_KEY", "from-env")
    assert secret("A_KEY") == "from-env"
    (app / ".env").chmod(0o600)
    (data / ".env").chmod(0o644)
    assert exposed_env_files() == [data / ".env"]
    (data / ".env").chmod(0o600)
    assert exposed_env_files() == []


def test_transient_errors_are_retried(monkeypatch, tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"x": 7}'}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})

    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    llm = llm_mod.LLM(LLMConfig(base_url="http://llm.test/v1", dialect="openai", retries=4))
    llm.http = httpx.Client(base_url="http://llm.test/v1", transport=httpx.MockTransport(handler))
    assert llm.chat_json("s", "u", Out).x == 7 and len(calls) == 3

    def bad(req):
        return httpx.Response(400, json={"error": "invalid argument"})

    llm.http = httpx.Client(base_url="http://llm.test/v1", transport=httpx.MockTransport(bad))
    with pytest.raises(llm_mod.LLMError, match="400"):
        llm.chat_json("s", "other", Out)  # a 4xx isn't retried


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def parse(self, **kw):
        self.kwargs = kw
        return self.response


def fake_client(stop="end_turn", parsed=Out(x=3)):
    msgs = FakeMessages(SimpleNamespace(stop_reason=stop, stop_details=None, parsed_output=parsed,
                                        usage=SimpleNamespace(input_tokens=100, output_tokens=20)))
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_anthropic_dialect(tmp_path, monkeypatch):
    cfg = LLMConfig(dialect="anthropic", model="claude-opus-5-5", effort="low", effort_thinking="high")
    llm = llm_mod.LLM(cfg, cache_dir=tmp_path / "cache")
    api, msgs = fake_client()
    llm._anthropic = api
    before = dict(llm_mod.USAGE)
    assert llm.chat_json("sys", "user", Out, think=True).x == 3
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["output_format"] is Out and kw["fallbacks"] == "default"
    assert kw["betas"] == [llm_anthropic.FALLBACK_BETA] and kw["output_config"] == {"effort": "high"}
    assert kw["max_tokens"] >= llm_anthropic.MIN_MAX_TOKENS and "temperature" not in kw
    assert llm_mod.USAGE["input"] - before["input"] == 100
    msgs.kwargs = None
    assert llm.chat_json("sys", "user", Out, think=True).x == 3 and msgs.kwargs is None  # cached

    for stop, msg in (("refusal", "declined"), ("max_tokens", "max_tokens")):
        llm._anthropic, _ = fake_client(stop=stop)
        with pytest.raises(llm_mod.LLMError, match=msg):
            llm.chat_json("sys", f"user {stop}", Out)
