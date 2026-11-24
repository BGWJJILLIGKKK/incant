"""Configuration.

Search order (later files override earlier ones):
  --config FILE                         if given, this file and its .local sibling (plus the data
                                        directory's incant.local.toml)
  otherwise the first directory of [cwd, $INCANT_HOME] containing incant.toml,
  reading incant.toml then incant.local.toml from it.

Relative data_dir and voices_dir resolve against the config file's directory; work_dir and output_dir
against the data directory.

Layout:
  [pipeline]            default method per stage when the CLI omits it
  [llm] [audio] [paths] [cast]   shared settings
  [<stage>.<method>]    per-method settings, validated by that method's Settings model

Data (books, work/, output/, cookies, personal settings) lives in the data directory: $INCANT_DATA, else
INCANT_DATA=… in the .env next to incant.toml (docker compose reads the same file), else [paths] data_dir,
else the config's own directory. An incant.local.toml in the data directory is read last.
"""

from __future__ import annotations

import os
import re
import tomllib
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LLMConfig(BaseModel):
    """Any OpenAI-compatible chat server: llama.cpp / llama-swap, vLLM, Ollama (http://localhost:11434/v1),
    LM Studio, OpenRouter, OpenAI. `dialect` picks the extra request fields the server understands."""

    model_config = ConfigDict(extra="allow")

    active: str | None = None  # name of the active LLM profile (when set in [llm])
    base_url: str = "http://localhost:8080/v1"
    model: str = "qwen3.5-9b"
    # The key's environment variable, read from the process environment or a .env file (data directory first).
    # Keys never go in a TOML file: the dashboard shows and edits those.
    api_key_env: str | None = None
    # "llama.cpp": Qwen-style thinking switch (chat_template_kwargs) and top_k/min_p sampling (also vLLM, SGLang).
    # "openai": standard fields only; thinking is off and the thinking sampling extras are dropped
    #           (OpenAI, Ollama, LM Studio, OpenRouter, most hosted APIs).
    # "anthropic": Claude through the official Anthropic SDK (extra: anthropic).
    dialect: Literal["llama.cpp", "openai", "anthropic"] = "llama.cpp"
    effort: str = "low"  # anthropic: thinking depth for ordinary calls
    effort_thinking: str = "high"  # anthropic: for calls a stage marks as "think"
    # Structured output: "json_schema" (constrained decoding), or "json_object" for servers without schemas
    # (the schema is then described in the prompt and the answer validated).
    structured: Literal["json_schema", "json_object"] = "json_schema"
    # "simple": references inlined, length limits dropped — for servers that reject parts of JSON Schema (Gemini).
    # The answer is still validated against the full schema.
    schema_style: Literal["full", "simple"] = "full"
    # Reasoning models (Gemini 3.x, OpenAI o-series): hidden reasoning counts against max_tokens.
    reasoning_effort: str | None = None  # openai dialect: sent as reasoning_effort ("none", "low", "medium", "high")
    min_max_tokens: int = 0  # floor for every request's max_tokens (room for the reasoning)
    retries: int = 4  # transient failures (network, 429, 5xx) are retried with backoff (2-32 s, or Retry-After)
    timeout_s: float = 1200.0  # long thinking calls share the GPU 4 ways (~25 tok/s each)
    concurrency: int = 4  # llama.cpp slots
    # Total KV cache shared by ALL slots (llama.cpp unified KV). Concurrent requests' prompt + max output
    # must fit in it together, or the server fails them all with "Context size has been exceeded".
    context_tokens: int = 32768
    temperature: float = 0.0  # non-thinking calls: greedy
    # Thinking calls loop under greedy decoding; Qwen's recommended thinking-mode sampling, fixed seed.
    thinking_sampling: dict = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "seed": 1234}

    @model_validator(mode="before")
    @classmethod
    def _no_inline_key(cls, data):
        if isinstance(data, dict) and "api_key" in data:
            raise ValueError('api_key is no longer read from configuration files: put the key in a .env file '
                             '(KEY_NAME=…, mode 600) and set api_key_env = "KEY_NAME"')
        return data


class AudioConfig(BaseModel):
    sample_rate: int = 22050  # IndexTTS native rate; other backends get resampled
    # Level matching: one gain per voice brings each voice's median segment loudness to voice_target_lufs
    # (voices differ by ~15 dB out of IndexTTS); dynamics within a voice are kept. Chapter loudnorm follows.
    level_match: bool = True
    voice_target_lufs: float = -21.0
    max_voice_gain_db: float = 12.0
    target_lufs: float = -19.0
    true_peak_db: float = -3.0
    pause_segment_ms: int = 150
    pause_span_ms: int = 300
    pause_paragraph_ms: int = 650
    pause_title_ms: int = 1200
    pause_scene_break_ms: int = 1600
    pause_chapter_end_ms: int = 2000


class PathsConfig(BaseModel):
    # Where books and audio live: work_dir and output_dir resolve against it (default: the config's directory).
    # $INCANT_DATA overrides it, and an incant.local.toml there is read last (containers: /data).
    data_dir: Path = Path(".")
    work_dir: Path = Path("work")
    voices_dir: Path = Path("voices")
    output_dir: Path = Path("output")  # finished books: <output_dir>/<author>/<title>/<title>.<ext>


class CastConfig(BaseModel):
    narrator: str | None = None  # voice id for the narrator and fallback for uncast speakers
    main_unique: int = 8  # the N speakers with most lines never share a voice
    # First-person books: narrate in the narrating character's own voice ("character") or the narrator voice.
    first_person_narration: str = "character"


class ReviewConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    public_domains: list[str] = Field(default_factory=list)
    trusted_proxy_ips: list[str] = Field(default_factory=list)

    @field_validator("public_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        domains = [value.lower() for value in values]
        if any(len(domain) > 253 or not re.fullmatch(rf"(?:{label}\.)+{label}", domain)
               or domain.rsplit(".", 1)[-1].isdigit() for domain in domains):
            raise ValueError("public_domains must contain exact DNS names without schemes, ports, or wildcards")
        return domains

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_proxy_ips(cls, values: list[str]) -> list[str]:
        if any("%" in value for value in values):
            raise ValueError("trusted_proxy_ips must contain unscoped IP addresses")
        return [str(ip_address(value)) for value in values]


class RunConfig(BaseModel):
    # A failed run deletes the work directory of a book it created ("new"), so half-made books (a gate page
    # ingested as text, a stage that crashed) don't linger or get resumed. Books that existed before the run,
    # and books with synthesized audio (hours of TTS), are never deleted. "never" keeps everything.
    cleanup_failed: Literal["new", "never"] = "new"
    # Batches run books in parallel lanes, one book at a time per server (lanes.py): "host" puts every server on
    # one machine in one lane (they may share a GPU); "endpoint" gives each server address its own lane;
    # "off" runs one book at a time.
    lanes: Literal["host", "endpoint", "off"] = "host"


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")  # [<stage>] tables land here

    pipeline: dict[str, str] = {}
    current_llm: str | None = None
    llm: LLMConfig = LLMConfig()
    llm_profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    audio: AudioConfig = AudioConfig()
    paths: PathsConfig = PathsConfig()
    cast: CastConfig = CastConfig()
    review: ReviewConfig = ReviewConfig()
    run: RunConfig = RunConfig()
    base_dir: Path = Path(".")
    files: list[Path] = []

    def method_settings(self, stage: str, method: str) -> dict[str, Any]:
        table = (self.model_extra or {}).get(stage) or {}
        return dict(table.get(method) or {})

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (self.base_dir / p)


_SECRET_FILES: list[Path] = []  # set by load_config: the data directory's .env, then the config's own


def secret(name: str | None) -> str | None:
    """A secret by variable name: the process environment, then the .env files of the last loaded config
    (data directory first). Values never reach logs, fingerprints or pipeline.json."""
    if not name:
        return None
    if name in os.environ:
        return os.environ[name]
    for f in _SECRET_FILES:
        if (v := env_file(f).get(name)) is not None:
            return v
    return None


def exposed_env_files() -> list[Path]:
    """.env files holding secrets that other users on this machine can read."""
    out = []
    for f in _SECRET_FILES:
        try:
            mode = f.stat().st_mode
        except OSError:
            continue
        names = env_file(f)
        if mode & 0o077 and any(re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I) for k in names):
            out.append(f)
    return out


def env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (the one docker compose also reads); {} if there is none."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _resolve_llm_profile(data: dict, current_llm: str | None = None) -> None:
    """Resolve active LLM profile (from current_llm, INCANT_LLM, [llm] active/current, or top-level setting)."""
    profiles: dict[str, dict] = {}

    llm_dict = dict(data.get("llm") or {})
    # Extract any sub-tables under [llm] (e.g. [llm.gemini], [llm.claude])
    for k, v in list(llm_dict.items()):
        if isinstance(v, dict):
            profiles[k] = v
            del llm_dict[k]

    # Extract any tables under [llms] (e.g. [llms.gemini])
    llms_dict = data.get("llms") or {}
    if isinstance(llms_dict, dict):
        for k, v in llms_dict.items():
            if isinstance(v, dict):
                profiles[k] = v

    # Extract any top-level tables matching profile conventions:
    # e.g. [allm], [bllm], or tables containing LLM keys like 'model' or 'dialect'
    standard_tables = {
        "pipeline", "audio", "paths", "cast", "review", "run", "llm", "llms",
        "credits", "words", "preingest", "ingest", "parse", "attribute", "normalize", "synthesize", "output"
    }
    for k, v in data.items():
        if k not in standard_tables and isinstance(v, dict) and (k.endswith("llm") or "model" in v or "dialect" in v):
            profiles[k] = v

    target = (
        current_llm
        or os.environ.get("INCANT_LLM")
        or data.get("current_llm")
        or llm_dict.get("active")
        or llm_dict.get("current")
    )
    if target is not None:
        target = str(target).strip()

    if target:
        if target in profiles:
            selected = profiles[target]
        elif target in data and target not in standard_tables and isinstance(data[target], dict):
            selected = data[target]
            profiles[target] = selected
        else:
            avail = ", ".join(sorted(profiles)) or "none"
            raise ValueError(f"Unknown LLM profile '{target}' (available: {avail})")

        llm_dict = _deep_merge(llm_dict, selected)
        llm_dict["active"] = target
        data["current_llm"] = target

    data["llm"] = llm_dict
    data["llm_profiles"] = profiles


def config_paths(path: Path | None = None) -> tuple[Path, Path | None, list[Path]]:
    if path:
        base = path.resolve().parent
        files = [path.resolve(), path.with_name(path.stem + ".local.toml").resolve()]
    else:
        cwd = Path.cwd()
        home = Path(os.environ["INCANT_HOME"]).resolve() if os.environ.get("INCANT_HOME") else None
        dirs = [cwd, home] if home is not None else [cwd]
        if home is not None and os.environ.get("INCANT_DATA") and cwd == Path(os.environ["INCANT_DATA"]).expanduser().resolve():
            dirs = [home, cwd]  # the mounted base override keeps the installation's resource directory
        base = next((d for d in dirs if (d / "incant.toml").exists()), Path.cwd()).resolve()
        files = [base / "incant.toml", base / "incant.local.toml"]
    data_env = os.environ.get("INCANT_DATA") or env_file(base / ".env").get("INCANT_DATA")
    data_dir = Path(data_env).expanduser() if data_env else None
    if data_dir is not None and not data_dir.is_absolute():
        data_dir = base / data_dir
    if data_dir and (extra := data_dir / "incant.local.toml") not in files:
        files.append(extra)  # the user's own settings and secrets, outside the program's directory
    return base, data_dir, files


def load_config(path: Path | None = None, *, current_llm: str | None = None,
                edits: dict[Path, str] | None = None, publish_secrets: bool = True) -> Config:
    base, data_dir, files = config_paths(path)
    edits = edits or {}
    # A daemon can edit the base configuration in the mounted data directory of a read-only image.
    # Keep resource paths relative to the original installation.
    if path is None and data_dir and ((replacement := data_dir / "incant.toml").is_file() or replacement in edits):
        files[0] = replacement
    data: dict = {}
    used = []
    for f in files:
        if f in edits or f.exists():
            data = _deep_merge(data, tomllib.loads(edits[f] if f in edits else f.read_text()))
            used.append(f)
    _resolve_llm_profile(data, current_llm)
    cfg = Config.model_validate({**data, "base_dir": base.resolve(), "files": used})
    if data_dir:
        cfg.paths.data_dir = data_dir
    cfg.paths.data_dir = cfg.resolve(cfg.paths.data_dir.expanduser()).resolve()
    cfg.paths.work_dir = cfg.paths.data_dir / cfg.paths.work_dir  # (an absolute path stays as it is)
    cfg.paths.output_dir = cfg.paths.data_dir / cfg.paths.output_dir
    cfg.paths.voices_dir = cfg.resolve(cfg.paths.voices_dir)
    if publish_secrets:
        _SECRET_FILES[:] = list(dict.fromkeys([cfg.paths.data_dir / ".env", base.resolve() / ".env"]))
    return cfg
