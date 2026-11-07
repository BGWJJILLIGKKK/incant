"""Stage/method discovery, CLI token resolution, and execution."""

from __future__ import annotations

import difflib
import importlib
import inspect
import json
import pkgutil
from dataclasses import dataclass
from pathlib import Path

from . import stages as stages_pkg
from .config import Config
from .stages.base import Context, Method, hash_paths
from .workdir import WorkDir, atomic_write

OUTPUT_SUFFIXES = {".m4b", ".m4a", ".mp3", ".mp4", ".flac", ".wav", ".opus"}

# ------------------------------------------------------------------ discovery


@dataclass
class StageInfo:
    name: str
    order: int
    description: str
    path: Path
    module: object

    @property
    def methods(self) -> list[str]:
        """Method directories: <name>/ with __init__.py and <name>.py. Found by scanning, no imports."""
        return sorted(
            d.name for d in self.path.iterdir()
            if d.is_dir() and not d.name.startswith(("_", ".")) and (d / "__init__.py").exists() and (d / f"{d.name}.py").exists()
        )


def discover() -> list[StageInfo]:
    out = []
    for mi in pkgutil.iter_modules(stages_pkg.__path__):
        if not mi.ispkg or mi.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{stages_pkg.__name__}.{mi.name}")
        if not hasattr(mod, "ORDER") or not hasattr(mod, "Base"):
            continue
        out.append(StageInfo(mi.name, mod.ORDER, getattr(mod, "DESCRIPTION", ""), Path(mod.__file__).parent, mod))
    return sorted(out, key=lambda s: s.order)


def load_method_class(stage: StageInfo, name: str) -> type[Method]:
    if name not in stage.methods:
        raise LookupError(f"stage '{stage.name}' has no method '{name}' (have: {', '.join(stage.methods)})")
    mod = importlib.import_module(f"{stages_pkg.__name__}.{stage.name}.{name}.{name}")
    base = stage.module.Base
    found = [c for _, c in inspect.getmembers(mod, inspect.isclass)
             if issubclass(c, base) and c is not base and c.__module__ == mod.__name__]
    if len(found) != 1:
        raise TypeError(f"{mod.__name__} must define exactly one {base.__name__} subclass (found {len(found)})")
    cls = found[0]
    cls.stage, cls.name = stage.name, name
    return cls


def instantiate(cfg: Config, stage: StageInfo, name: str) -> Method:
    cls = load_method_class(stage, name)
    settings = cls.Settings.model_validate(cfg.method_settings(stage.name, name))
    return cls(settings, cfg)


# ------------------------------------------------------------------ resolution


@dataclass
class Step:
    stage: StageInfo
    method: str
    why: str  # "cli" | "config" | "auto" | "only" | "existing"


class ResolveError(ValueError):
    pass


def resolve(tokens: list[str], source: str | None, cfg: Config, stages: list[StageInfo],
            resuming: bool = False) -> tuple[list[Step], Path | None]:
    """Map free-form CLI tokens to stages.

    - `stage=method` / `stage:method` is explicit.
    - A bare name unique to one stage goes to that stage, wherever it appears.
    - A name shared by several stages goes to the first such stage after the previous token's stage.
    - A trailing token that isn't a method but looks like a file (e.g. out.m4b) is the output path.
    - Stages left over take [pipeline] defaults from config, then 'auto' guessing, then a sole method.
    """
    by_name = {s.name: s for s in stages}
    owners: dict[str, list[StageInfo]] = {}
    for s in stages:
        for m in s.methods:
            owners.setdefault(m, []).append(s)

    chosen: dict[str, str] = {}
    out_path: Path | None = None
    last_order = -1
    for i, tok in enumerate(tokens):
        sep = "=" if "=" in tok else (":" if ":" in tok and not Path(tok).suffix else None)
        if sep:
            st, m = tok.split(sep, 1)
            if st not in by_name:
                raise ResolveError(f"unknown stage '{st}' (stages: {', '.join(by_name)})")
            if m not in by_name[st].methods:
                raise ResolveError(f"stage '{st}' has no method '{m}' (have: {', '.join(by_name[st].methods)})")
            cand = [by_name[st]]
        else:
            cand = owners.get(tok, [])
        if not cand:
            if i == len(tokens) - 1 and (Path(tok).suffix.lower() in OUTPUT_SUFFIXES or "/" in tok):
                out_path = Path(tok)
                continue
            all_methods = sorted(owners)
            hint = difflib.get_close_matches(tok, all_methods, n=3)
            raise ResolveError(f"'{tok}' is not a method of any stage" + (f" — did you mean {', '.join(hint)}?" if hint else "")
                               + f"\n  available: " + "; ".join(f"{s.name}: {', '.join(s.methods)}" for s in stages))
        free = [s for s in cand if s.name not in chosen]
        if not free:
            raise ResolveError(f"'{tok}': stage {'/'.join(s.name for s in cand)} already set to '{chosen[cand[0].name]}'")
        if len(free) > 1:
            after = [s for s in free if s.order > last_order]
            free = after or free
        st = free[0]
        chosen[st.name] = tok.split(sep, 1)[1] if sep else tok
        last_order = st.order

    steps: list[Step] = []
    for s in stages:
        if s.name in chosen:
            steps.append(Step(s, chosen[s.name], "cli"))
            continue
        if resuming and s.order == stages[0].order:
            steps.append(Step(s, "-", "existing"))
            continue
        default = cfg.pipeline.get(s.name)
        if default == "auto" or (default is None and hasattr(s.module.Base, "accepts")):
            guess = _auto(s, source)
            if guess:
                steps.append(Step(s, guess, "auto"))
                continue
            raise ResolveError(f"stage '{s.name}': no method accepts '{source}' (have: {', '.join(s.methods)})")
        if default:
            if default not in s.methods:
                raise ResolveError(f"config [pipeline] {s.name} = '{default}' but that method doesn't exist (have: {', '.join(s.methods)})")
            steps.append(Step(s, default, "config"))
        elif len(s.methods) == 1:
            steps.append(Step(s, s.methods[0], "only"))
        else:
            raise ResolveError(f"stage '{s.name}': no method given and no [pipeline] default (choose: {', '.join(s.methods)})")
    return steps, out_path


def _auto(stage: StageInfo, source: str | None) -> str | None:
    if source is None:
        return None
    for m in stage.methods:
        cls = load_method_class(stage, m)
        if getattr(cls, "accepts", None) and cls.accepts(source):
            return m
    return None


# ------------------------------------------------------------------ execution


def _records_path(wd: WorkDir) -> Path:
    return wd.root / "pipeline.json"


def _read_records(wd: WorkDir) -> dict:
    p = _records_path(wd)
    return json.loads(p.read_text()) if p.exists() else {}


def run_pipeline(steps: list[Step], ctx: Context, *, start: str | None = None, stop: str | None = None,
                 force: set[str] | None = None) -> Context:
    """Run the steps; on failure, clean up a book this run created (see [run] cleanup_failed), then re-raise."""
    try:
        return _run_steps(steps, ctx, start=start, stop=stop, force=force)
    except Exception as e:
        cleanup_failed(ctx, e)
        raise


def cleanup_failed(ctx: Context, error: BaseException) -> bool:
    """Delete the work directory of a book created by this run. Kept: books that existed before the run (they
    may hold hand edits) and books with synthesized audio (expensive, and resumable). Returns True if deleted."""
    wd = ctx.wd
    if ctx.cfg.run.cleanup_failed == "never" or not ctx.created or wd is None or not wd.root.is_dir():
        return False
    if any(wd.cache_dir.glob("*/*.wav")):
        ctx.log(f"  keeping {wd.root}: it has synthesized audio (resume with --from)")
        return False
    root = wd.root.resolve()
    work = ctx.cfg.resolve(ctx.cfg.paths.work_dir).resolve()
    if root.parent != work:  # only ever a book directory directly under the work dir
        return False
    import shutil

    shutil.rmtree(root)
    ctx.log(f"  removed {root} (new book; the run failed: {type(error).__name__})")
    ctx.wd = None
    return True


def _run_steps(steps: list[Step], ctx: Context, *, start: str | None = None, stop: str | None = None,
               force: set[str] | None = None) -> Context:
    force = force or set()
    names = [s.stage.name for s in steps]
    for n in (start, stop):
        if n and n not in names:
            raise ResolveError(f"unknown stage '{n}' (stages: {', '.join(names)})")
    active = False if start else True
    upstream_hash = ""
    for step in steps:
        name = step.stage.name
        if start and name == start:
            active = True
        if step.method == "-" or not active:
            if ctx.wd:
                upstream_hash = hash_paths(step.stage.module.Base.outputs(ctx.wd))
            ctx.log(f"[{name}] skipped ({'existing book' if step.method == '-' else 'before --from'})")
        else:
            method = instantiate(ctx.cfg, step.stage, step.method)
            key = f"{method.fingerprint()}:{upstream_hash}"
            rec = _read_records(ctx.wd).get(name) if ctx.wd else None
            outs = method.outputs(ctx.wd) if ctx.wd else []
            fresh = (method.SKIP_IF_UNCHANGED and name not in force and "all" not in force and rec
                     and rec.get("key") == key and outs and all(p.exists() for p in outs))
            if fresh:
                ctx.log(f"[{name}] {step.method}: up to date")
            else:
                ctx.log(f"[{name}] {step.method}")
                ctx.results[name] = method.run(ctx)
                if ctx.wd:
                    recs = _read_records(ctx.wd)
                    recs[name] = {"method": step.method, "key": key}
                    atomic_write(_records_path(ctx.wd), json.dumps(recs, indent=2) + "\n")
            upstream_hash = hash_paths(method.outputs(ctx.need_wd()))
        if stop and name == stop:
            break
    return ctx
