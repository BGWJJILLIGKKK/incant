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


PUBLISHING_MODES = ("audiobook", "ebook")


def stages_for(stages: list[StageInfo], mode: str) -> list[StageInfo]:
    return [s for s in stages if mode in getattr(s.module, "MODES", ("audiobook",))]


def discover(mode: str | None = "audiobook") -> list[StageInfo]:
    out = []
    for mi in pkgutil.iter_modules(stages_pkg.__path__):
        if not mi.ispkg or mi.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{stages_pkg.__name__}.{mi.name}")
        if not hasattr(mod, "ORDER") or not hasattr(mod, "Base"):
            continue
        out.append(StageInfo(mi.name, mod.ORDER, getattr(mod, "DESCRIPTION", ""), Path(mod.__file__).parent, mod))
    ordered = sorted(out, key=lambda s: s.order)
    return ordered if mode is None else stages_for(ordered, mode)


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


def parent_methods(stage: StageInfo, cls: type) -> list[str]:
    """Methods of the same stage that `cls` inherits from, nearest first (mobi -> [ebook])."""
    out = []
    prefix = f"{stages_pkg.__name__}.{stage.name}."
    for base in cls.__mro__[1:]:
        mod = base.__module__
        if mod.startswith(prefix):
            parts = mod[len(prefix):].split(".")
            if len(parts) == 2 and parts[0] == parts[1] and parts[0] in stage.methods and parts[0] not in out:
                out.append(parts[0])
    return out


def instantiate(cfg: Config, stage: StageInfo, name: str) -> Method:
    """Settings come from [<stage>.<name>], on top of the tables of the methods it inherits from
    ([ingest.ebook] settings apply to [ingest.mobi] too)."""
    cls = load_method_class(stage, name)
    merged: dict = {}
    for parent in reversed(parent_methods(stage, cls)):
        merged = _merge(merged, cfg.method_settings(stage.name, parent))
    merged = _merge(merged, cfg.method_settings(stage.name, name))
    fields = cls.Settings.model_fields
    unknown = sorted(set(cfg.method_settings(stage.name, name)) - set(fields))
    if unknown:
        raise ValueError(f"[{stage.name}.{name}]: unknown setting(s) {', '.join(unknown)}"
                         + (f" (known: {', '.join(fields)})" if fields else " (this method has no settings)"))
    settings = cls.Settings.model_validate({k: v for k, v in merged.items() if k in fields})
    return cls(settings, cfg)


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ------------------------------------------------------------------ resolution


@dataclass
class Step:
    stage: StageInfo
    method: str
    why: str  # "cli" | "config" | "auto" | "only" | "default" | "existing"


class ResolveError(ValueError):
    pass


def resolve(tokens: list[str], source: str | None, cfg: Config, stages: list[StageInfo],
            resuming: bool = False) -> tuple[list[Step], Path | None]:
    """Map free-form CLI tokens to stages.

    - `stage=method` / `stage:method` is explicit.
    - A bare name unique to one stage goes to that stage, wherever it appears.
    - A name shared by several stages goes to the first such stage after the previous token's stage.
    - A trailing token that isn't a method but looks like a file (e.g. out.m4b) is the output path.
    - A trailing output path picks the output method writing that extension (out.mp3 -> mp3).
    - Stages left over take [pipeline] defaults from config, then 'auto' guessing, then a sole method,
      then the stage's own DEFAULT.
    - PRE stages (preingest) are consulted on the raw input before the pipeline runs; they get no step.
    """
    by_name = {s.name: s for s in stages}
    owners: dict[str, list[StageInfo]] = {}
    for s in stages:
        for m in s.methods:
            owners.setdefault(m, []).append(s)

    output_suffixes = OUTPUT_SUFFIXES | {".epub"} if "publish" in by_name else OUTPUT_SUFFIXES
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
            if i == len(tokens) - 1 and (Path(tok).suffix.lower() in output_suffixes or "/" in tok):
                out_path = Path(tok)
                continue
            all_methods = sorted(owners)
            hint = difflib.get_close_matches(tok, all_methods, n=3)
            raise ResolveError(f"'{tok}' is not a method of any stage" + (f" — did you mean {', '.join(hint)}?" if hint else "")
                               + "\n  available: " + "; ".join(f"{s.name}: {', '.join(s.methods)}" for s in stages))
        free = [s for s in cand if s.name not in chosen]
        if not free:
            raise ResolveError(f"'{tok}': stage {'/'.join(s.name for s in cand)} already set to '{chosen[cand[0].name]}'")
        if len(free) > 1:
            after = [s for s in free if s.order > last_order]
            free = after or free
        st = free[0]
        chosen[st.name] = tok.split(sep, 1)[1] if sep else tok
        last_order = st.order

    if out_path is not None:  # out.mp3 picks the output method that writes .mp3
        for s in stages:
            if s.name in chosen or not hasattr(s.module.Base, "EXTENSION"):
                continue
            hits = [m for m in s.methods if getattr(load_method_class(s, m), "EXTENSION", None) == out_path.suffix.lower()]
            if len(hits) == 1 and cfg.pipeline.get(s.name) not in hits:
                chosen[s.name] = hits[0]

    steps: list[Step] = []
    per_book = [s for s in stages if not getattr(s.module, "PRE", False)]
    for s in stages:
        if getattr(s.module, "PRE", False):
            continue  # consulted on the raw input before the pipeline (cli.expand_source), not a per-book step
        if s.name in chosen:
            steps.append(Step(s, chosen[s.name], "cli"))
            continue
        if resuming and s is per_book[0]:
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
        elif getattr(s.module, "DEFAULT", None) in s.methods:
            steps.append(Step(s, s.module.DEFAULT, "default"))
        else:
            raise ResolveError(f"stage '{s.name}': no method given and no [pipeline] default (choose: {', '.join(s.methods)})")
    return steps, out_path


def _auto(stage: StageInfo, source: str | None) -> str | None:
    """The accepting method; when several accept, the most specific one (a subclass over the method it
    inherits from: gutenberg over txt for Project Gutenberg files)."""
    if source is None:
        return None
    hits = {m: load_method_class(stage, m) for m in stage.methods}
    hits = {m: c for m, c in hits.items() if getattr(c, "accepts", None) and c.accepts(source)}
    specific = [m for m, c in hits.items() if not any(o is not c and issubclass(o, c) for o in hits.values())]
    return specific[0] if specific else None


# ------------------------------------------------------------------ execution


def _records_path(wd: WorkDir, *, ebook: bool = False) -> Path:
    return wd.root / ("ebook-pipeline.json" if ebook else "pipeline.json")


def read_records(wd: WorkDir, *, ebook: bool = False) -> dict:
    p = _records_path(wd, ebook=ebook)
    return json.loads(p.read_text()) if p.exists() else {}


def validate_stage_range(names: list[str], start: str | None, stop: str | None) -> None:
    for name in (start, stop):
        if name is not None and name not in names:
            raise ResolveError(f"unknown stage '{name}' (stages: {', '.join(names)})")
    if start is not None and stop is not None and names.index(start) > names.index(stop):
        raise ResolveError(f"--from {start} comes after --until {stop}")


def run_pipeline(steps: list[Step], ctx: Context, *, start: str | None = None, stop: str | None = None,
                 force: set[str] | None = None, log_skips: bool = True) -> Context:
    """Run the steps; on failure, clean up a book this run created (see [run] cleanup_failed), then re-raise."""
    try:
        return _run_steps(steps, ctx, start=start, stop=stop, force=force, log_skips=log_skips)
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


def _existing_book(ctx: Context) -> None:
    """--from past ingest with a source as input (a batch item, or a file named again): the book ingested from it."""
    from .stages.ingest.base import book_for_source, canonical_source

    work = ctx.cfg.paths.work_dir
    slug = ctx.slug if ctx.slug and (work / ctx.slug / "meta.json").exists() else \
        book_for_source(work, canonical_source(ctx.source or ""))
    if slug is None:
        raise RuntimeError(f"'{ctx.source}' hasn't been ingested yet: run it without --from first (a page that is "
                           "part of a series is stored under the series' address)")
    ctx.wd = WorkDir.for_slug(work, slug)


def _run_steps(steps: list[Step], ctx: Context, *, start: str | None = None, stop: str | None = None,
               force: set[str] | None = None, log_skips: bool = True) -> Context:
    force = force or set()
    names = [s.stage.name for s in steps]
    validate_stage_range(names, start, stop)
    active = False if start else True
    upstream_hash = ""
    checked_chapters = False
    for step in steps:
        name = step.stage.name
        if start and name == start:
            active = True
        if step.method == "-" or not active:
            if ctx.wd is None and ctx.source and step.method != "-":
                _existing_book(ctx)
            if ctx.wd:
                upstream_hash = hash_paths(step.stage.module.Base.outputs(ctx.wd))
            if log_skips:
                ctx.log(f"[{name}] skipped ({'existing book' if step.method == '-' else 'before --from'})")
        else:
            if ctx.chapters is not None and not checked_chapters and name in ("attribute", "normalize", "synthesize", "output"):
                available = {ch.id for ch in ctx.need_wd().read_chapters().chapters}
                if missing := ctx.chapters - available:
                    raise ResolveError(f"unknown chapter(s): {', '.join(sorted(missing))}")
                checked_chapters = True
            method = instantiate(ctx.cfg, step.stage, step.method)
            key = f"{method.fingerprint()}:{upstream_hash}"
            if name == "attribute":
                scope = "all" if ctx.chapters is None else ",".join(sorted(ctx.chapters))
                key += f":chapters={scope}"
            ebook = getattr(step.stage.module, "MODES", ()) == ("ebook",)
            rec = read_records(ctx.wd, ebook=ebook).get(name) if ctx.wd else None
            outs = method.outputs(ctx.wd) if ctx.wd else []
            if name == "attribute" and ctx.wd:
                outs = [ctx.wd.spans_path(ch.id) for ch in ctx.wd.read_chapters().chapters
                        if ctx.chapters is None or ch.id in ctx.chapters]
            fresh = (method.SKIP_IF_UNCHANGED and name not in force and "all" not in force and rec
                     and rec.get("key") == key and not rec.get("incomplete") and outs and all(p.exists() for p in outs))
            if fresh:
                ctx.log(f"[{name}] {step.method}: up to date")
            else:
                ctx.log(f"[{name}] {step.method}")
                ctx.results[name] = method.run(ctx)
                if ctx.wd:
                    recs = read_records(ctx.wd, ebook=ebook)
                    rec_entry = {"method": step.method, "key": key}
                    if name == "attribute" and (ctx.wd.root / "attribution_errors.json").exists():
                        rec_entry["incomplete"] = True
                    recs[name] = rec_entry
                    atomic_write(_records_path(ctx.wd, ebook=ebook), json.dumps(recs, indent=2) + "\n")
            upstream_hash = hash_paths(method.outputs(ctx.need_wd()))
        if stop and name == stop:
            break
    return ctx
