"""Configuration.

Search order (later files override earlier ones):
  --config FILE                         if given, only this file (+ its .local sibling)
  otherwise the first directory of [cwd, $INCANT_HOME] containing incant.toml,
  reading incant.toml then incant.local.toml from it.

Relative paths in [paths] resolve against the directory of the config file.

Layout:
  [pipeline]            default method per stage when the CLI omits it
  [llm] [audio] [paths] [cast]   shared settings
  [<stage>.<method>]    per-method settings, validated by that method's Settings model
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8080/v1"
    model: str = "qwen3.5-9b"
    api_key: str = "none"
    timeout_s: float = 1200.0  # long thinking calls share the GPU 4 ways (~25 tok/s each)
    concurrency: int = 4  # llama.cpp slots
    # Total KV cache shared by ALL slots (llama.cpp unified KV). Concurrent requests' prompt + max output
    # must fit in it together, or the server fails them all with "Context size has been exceeded".
    context_tokens: int = 32768
    temperature: float = 0.0  # non-thinking calls: greedy
    # Thinking calls loop under greedy decoding; Qwen's recommended thinking-mode sampling, fixed seed.
    thinking_sampling: dict = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "seed": 1234}


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
    work_dir: Path = Path("work")
    voices_dir: Path = Path("voices")
    output_dir: Path = Path("output")  # finished books: <output_dir>/<author>/<title>/<title>.<ext>


class CastConfig(BaseModel):
    narrator: str | None = None  # voice id for the narrator and fallback for uncast speakers
    main_unique: int = 8  # the N speakers with most lines never share a voice
    # First-person books: narrate in the narrating character's own voice ("character") or the narrator voice.
    first_person_narration: str = "character"


class ReviewConfig(BaseModel):
    host: str = "127.0.0.1"  # "0.0.0.0" to reach it from another machine (no authentication!)
    port: int = 8765


class RunConfig(BaseModel):
    # A failed run deletes the work directory of a book it created ("new"), so half-made books (a gate page
    # ingested as text, a stage that crashed) don't linger or get resumed. Books that existed before the run,
    # and books with synthesized audio (hours of TTS), are never deleted. "never" keeps everything.
    cleanup_failed: Literal["new", "never"] = "new"


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")  # [<stage>] tables land here

    pipeline: dict[str, str] = {}
    llm: LLMConfig = LLMConfig()
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


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: Path | None = None) -> Config:
    if path:
        files = [path, path.with_name(path.stem + ".local.toml")]
        base = path.resolve().parent
    else:
        dirs = [Path.cwd()] + ([Path(os.environ["INCANT_HOME"])] if os.environ.get("INCANT_HOME") else [])
        base = next((d for d in dirs if (d / "incant.toml").exists()), Path.cwd())
        files = [base / "incant.toml", base / "incant.local.toml"]
    data: dict = {}
    used = []
    for f in files:
        if f.exists():
            data = _deep_merge(data, tomllib.loads(f.read_text()))
            used.append(f)
    cfg = Config.model_validate({**data, "base_dir": base.resolve(), "files": used})
    cfg.paths.work_dir = cfg.resolve(cfg.paths.work_dir)
    cfg.paths.voices_dir = cfg.resolve(cfg.paths.voices_dir)
    cfg.paths.output_dir = cfg.resolve(cfg.paths.output_dir)
    return cfg
