"""The interface every method in every stage implements.

A stage is a package under incant/stages/ whose __init__.py defines:
    ORDER: int            position in the pipeline
    DESCRIPTION: str
    Base: type[Method]    the stage's method base class (stage-specific hooks)

A method is a directory incant/stages/<stage>/<name>/ containing __init__.py
and <name>.py; <name>.py defines exactly one subclass of the stage's Base.
Plain .py files in a stage directory are shared helpers, not methods.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ..config import Config
from ..workdir import WorkDir


class NoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class Context:
    """Mutable state passed along the pipeline."""

    cfg: Config
    source: str | None = None  # stage 1 input
    wd: WorkDir | None = None  # set by ingest (or by the CLI when resuming a book)
    created: bool = False  # ingest made this book's work directory in this run (cleanup on failure)
    slug: str | None = None
    title: str | None = None  # title hint for ingest (from a container such as a bookmark file)
    out: Path | None = None  # final output path override
    out_dir: Path | None = None  # base of the output tree (-o DIR); default paths.output_dir
    chapters: set[str] | None = None  # restrict per-chapter stages to these ids
    log: Callable[[str], None] = field(default=lambda msg: print(msg, flush=True))
    results: dict[str, object] = field(default_factory=dict)  # stage name -> return value

    def need_wd(self) -> WorkDir:
        if self.wd is None:
            raise RuntimeError("no book work directory (ingest has not run)")
        return self.wd


class Method:
    stage: ClassVar[str] = ""  # filled in by the loader
    name: ClassVar[str] = ""  # filled in by the loader
    Settings: ClassVar[type[BaseModel]] = NoSettings

    # Skip when re-run with the same method, settings and upstream outputs.
    # Expensive or hand-editable stages set True; cheap/cached ones leave False.
    SKIP_IF_UNCHANGED: ClassVar[bool] = False

    # Credits (optional; see credits.py). LICENSE: the component's own license. OUTPUT_TERMS: what using it
    # imposes on the finished audiobook — "none", "attribution: …", "provider terms: …", "non-commercial: …";
    # None = unknown (reported as a gap, never assumed). USES_LLM: terms come from the configured [llm] model.
    LICENSE: ClassVar[str | None] = None
    OUTPUT_TERMS: ClassVar[str | None] = None
    USES_LLM: ClassVar[bool] = False

    def __init__(self, settings: BaseModel, cfg: Config):
        self.settings = settings
        self.cfg = cfg

    def run(self, ctx: Context) -> object:
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        """Files/dirs this stage produces (same for every method of a stage); hashed to decide
        whether downstream stages are stale."""
        return []

    def check(self) -> list[tuple[bool, str]]:
        """Readiness checks for `--check`: (ok, message)."""
        return []

    def fingerprint(self) -> str:
        blob = json.dumps([self.stage, self.name, self.settings.model_dump(mode="json")], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def resource(self) -> str | None:
        """What this method waits on, for batch lanes (lanes.py): a server's 'host:port', a name for an
        in-process engine, or None for quick local work. A batch sends one book at a time to each resource."""
        if self.USES_LLM:
            from ..lanes import llm_endpoint

            return llm_endpoint(self.cfg.llm)
        return None


def hash_paths(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        files = sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p]
        for f in files:
            if f.exists():
                h.update(str(f.name).encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]
