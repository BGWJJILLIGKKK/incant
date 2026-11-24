"""Tests for per-LLM profiles and context size configuration."""

import pytest
from pydantic import BaseModel

from incant import llm as llm_mod
from incant.config import LLMConfig, load_config


class DummyOut(BaseModel):
    val: str


def test_backward_compatibility_single_llm(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
[llm]
model = "qwen3.5-9b"
context_tokens = 32768
concurrency = 4
""")
    cfg = load_config(conf)
    assert cfg.current_llm is None
    assert cfg.llm.model == "qwen3.5-9b"
    assert cfg.llm.context_tokens == 32768
    assert cfg.llm.concurrency == 4


def test_current_llm_selects_subtable_profile(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "gemini"

[llm]
timeout_s = 600.0
temperature = 0.1

[llm.local]
model = "qwen3.5-9b"
context_tokens = 32768

[llm.gemini]
dialect = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.8-flash"
api_key_env = "GEMINI_API_KEY"
schema_style = "simple"
reasoning_effort = "low"
min_max_tokens = 16000
context_tokens = 1000000
concurrency = 8
""")
    cfg = load_config(conf)
    assert cfg.current_llm == "gemini"
    assert cfg.llm.model == "gemini-3.8-flash"
    assert cfg.llm.context_tokens == 1000000
    assert cfg.llm.concurrency == 8
    assert cfg.llm.dialect == "openai"
    assert cfg.llm.schema_style == "simple"
    assert cfg.llm.reasoning_effort == "low"
    assert cfg.llm.min_max_tokens == 16000
    # Base settings inherited:
    assert cfg.llm.timeout_s == 600.0
    assert cfg.llm.temperature == 0.1


def test_active_inside_llm_table(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
[llm]
active = "claude"

[llm.claude]
dialect = "anthropic"
model = "claude-opus-5-5"
api_key_env = "CLAUDE_API_KEY"
context_tokens = 1000000
""")
    cfg = load_config(conf)
    assert cfg.current_llm == "claude"
    assert cfg.llm.active == "claude"
    assert cfg.llm.model == "claude-opus-5-5"
    assert cfg.llm.dialect == "anthropic"
    assert cfg.llm.context_tokens == 1000000


def test_top_level_chunks_selection(tmp_path):
    """Matches user's current incant.local.toml pattern with top-level [allm] and [bllm]."""
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "bllm"

[allm]
dialect = "anthropic"
model = "claude-opus-5-5"
context_tokens = 1000000

[bllm]
dialect = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.8-flash"
context_tokens = 1000000
schema_style = "simple"

[llm]
base_url = "http://virt:8080/v1"
model = "qwen3.5-9b"
context_tokens = 32768
""")
    cfg = load_config(conf)
    assert cfg.current_llm == "bllm"
    assert cfg.llm.model == "gemini-3.8-flash"
    assert cfg.llm.context_tokens == 1000000
    assert cfg.llm.dialect == "openai"
    assert cfg.llm.schema_style == "simple"


def test_cli_override_beats_file_setting(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "local"

[llm.local]
model = "qwen3.5-9b"
context_tokens = 32768

[llm.gemini]
model = "gemini-3.8-flash"
context_tokens = 1000000
""")
    cfg = load_config(conf, current_llm="gemini")
    assert cfg.current_llm == "gemini"
    assert cfg.llm.model == "gemini-3.8-flash"
    assert cfg.llm.context_tokens == 1000000


def test_env_var_override(tmp_path, monkeypatch):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "local"

[llm.local]
model = "qwen3.5-9b"
context_tokens = 32768

[llm.gemini]
model = "gemini-3.8-flash"
context_tokens = 1000000
""")
    monkeypatch.setenv("INCANT_LLM", "gemini")
    cfg = load_config(conf)
    assert cfg.current_llm == "gemini"
    assert cfg.llm.model == "gemini-3.8-flash"
    assert cfg.llm.context_tokens == 1000000


def test_unknown_profile_raises_helpful_error(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "nonexistent"

[llm.local]
model = "qwen3.5-9b"

[llm.gemini]
model = "gemini-3.8-flash"
""")
    with pytest.raises(ValueError, match="Unknown LLM profile 'nonexistent'.*available:.*gemini.*local"):
        load_config(conf)


def test_token_budget_isolation_per_profile():
    local_cfg = LLMConfig(
        base_url="http://localhost:8080/v1",
        model="qwen3.5-9b",
        context_tokens=32768,
    )
    cloud_cfg = LLMConfig(
        dialect="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-3.8-flash",
        context_tokens=1000000,
    )
    anthropic_cfg = LLMConfig(
        dialect="anthropic",
        model="claude-opus-5-5",
        context_tokens=500000,
    )

    llm_local = llm_mod.LLM(local_cfg)
    llm_cloud = llm_mod.LLM(cloud_cfg)
    llm_anthropic = llm_mod.LLM(anthropic_cfg)

    assert llm_local.budget.total == 32768
    assert llm_cloud.budget.total == 1000000
    assert llm_anthropic.budget.total == 500000

    # Acquire tokens from local budget and verify cloud budgets are unaffected
    need = llm_local.budget.acquire(10000)
    assert llm_local.budget.free == 32768 - 10000
    assert llm_cloud.budget.free == 1000000
    assert llm_anthropic.budget.free == 500000
    llm_local.budget.release(need)
    assert llm_local.budget.free == 32768


def test_cli_handles_invalid_llm_profile_cleanly(tmp_path, capsys):
    from incant.cli import main

    conf = tmp_path / "incant.toml"
    conf.write_text("[llm]\nmodel = 'qwen'\n")
    code = main(["--llm", "invalid", "-c", str(conf)])
    assert code == 2
    err = capsys.readouterr().err
    assert "error: Unknown LLM profile 'invalid'" in err


def test_standard_tables_not_treated_as_profiles(tmp_path):
    conf = tmp_path / "incant.toml"
    conf.write_text("""
current_llm = "preingest"

[preingest]
model = "not-an-llm"
""")
    with pytest.raises(ValueError, match="Unknown LLM profile 'preingest'"):
        load_config(conf)
