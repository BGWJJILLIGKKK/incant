"""./incant INPUT [METHOD ...] [OUTPUT] [options]

The pipeline is spelled out by method names, one per stage, in stage order:

    ./incant book.epub epub basic narrator indextts m4b
    ./incant book.epub indextts                 # other stages from [pipeline] / auto-detect
    ./incant book.epub dummy out/test.m4b       # trailing file path = output location
    ./incant pride-and-prejudice synthesize=indextts --from synthesize   # resume an ingested book
    ./incant pride-and-prejudice --review       # review/edit page at http://127.0.0.1:8765/

INPUT is a file, a URL, an existing book slug under work/, or a book work directory. A relative path that
doesn't exist where you are is looked up in the data directory ($INCANT_DATA / .env), so
`./incant books/x.epub` finds <data>/books/x.epub. A directory of
source files (no meta.json in it) is a batch: every supported file in it, by name, each through the whole
pipeline; a failing book is reported and the batch continues. Books overlap where they use different servers,
one book at a time per server ([run] lanes).

    ./incant books/                            # every supported file in books/
    ./incant books/ dummy --until synthesize   # same methods/options for each
    ./incant books/ -o audiobooks/             # output tree under audiobooks/ instead of output/

A container file (a browser bookmark export) is a batch too: each page in it is its own book. The
preingest stage decides how an input is ingested: as one book, or expanded into a batch. Inside a
batch directory, containers are expanded in place. Two sources with the same title get separate books
(slug, slug-2, …); re-running the same source reuses its book.

Finished books go to one output tree, [paths] output_dir (default output/):
    output/<author>/<title>/<title>.m4b      ("Unknown Author" when the source names none)
-o FILE writes one book to that file instead; -o DIR (or a trailing directory) moves the tree.

    ./incant bookmarks.html --until attribute
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import load_config
from .lanes import Job, describe, plan_phases, run_lanes
from .pipeline import ResolveError, StageInfo, Step, discover, instantiate, load_method_class, resolve, run_pipeline
from .pipeline import PUBLISHING_MODES, validate_stage_range
from .stages.base import Context
from .workdir import ProtectedFileError, WorkDir, is_protected_file, refuse_protected


def _parse_chapters(spec: str | None) -> set[str] | None:
    if spec is None:
        return None
    out: set[str] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        try:
            first, last = int(a), int(b if "-" in part else a)
            if not 1 <= first <= last:
                raise ValueError
        except ValueError:
            raise ResolveError(f"invalid chapter selection '{part}' (use positive numbers or ascending ranges, e.g. 1-3,7)") from None
        out.update(f"{n:04d}" for n in range(first, last + 1))
    return out


def _doc(cls) -> str:
    own = cls.__dict__.get("__doc__")  # not inherited from the stage base
    d = inspect.cleandoc(own) if own else (inspect.getdoc(sys.modules[cls.__module__]) or "")
    return d.splitlines()[0] if d else ""


def cmd_list(stages: list[StageInfo], cfg) -> int:
    """Stages and their methods; a method that inherits from another is listed under it."""
    from .pipeline import parent_methods

    for s in stages:
        default = cfg.pipeline.get(s.name)
        print(f"{s.order}. {s.name:<11} {s.description}")
        docs, parent = {}, {}
        for m in s.methods:
            try:
                cls = load_method_class(s, m)
                docs[m] = _doc(cls)
                parent[m] = next(iter(parent_methods(s, cls)), None)
            except Exception as e:  # noqa: BLE001
                docs[m], parent[m] = f"(failed to load: {e})", None
        rows: list[tuple[str, str]] = []  # (displayed name, method)

        def walk(m: str, depth: int) -> None:
            rows.append((("   " * (depth - 1) + "└─ " if depth else "") + m, m))
            for child in sorted(c for c, p in parent.items() if p == m):
                walk(child, depth + 1)

        for m in s.methods:
            if parent[m] is None:
                walk(m, 0)
        width = max(len(n) for n, _ in rows) + 2
        for name, m in rows:
            mark = "*" if m == default or (default is None and m == getattr(s.module, "DEFAULT", None)) else " "
            print(f"   {mark} {name:<{width}} {docs[m]}")
        if default == "auto":
            print("   * auto       (detected from the input; the most specific accepting method wins)")
    print("\n* = default ([pipeline] in config, else the stage's own)")
    if any(stage.name == "attribute" for stage in stages):
        if cfg.current_llm:
            print(f"LLM: {cfg.current_llm} (model: '{cfg.llm.model}', context: {cfg.llm.context_tokens:,} tokens)")
        else:
            print(f"LLM: '{cfg.llm.model}' (context: {cfg.llm.context_tokens:,} tokens)")
    return 0


@dataclass
class BatchItem:
    label: str  # shown in progress and the summary
    source: str
    title: str | None = None  # title hint from a container


def _pre_stages(stages: list[StageInfo]) -> list[StageInfo]:
    """Stages consulted on the raw input before the pipeline (preingest)."""
    return [s for s in stages if getattr(s.module, "PRE", False)]


def _expanders(stages: list[StageInfo]) -> dict[str, type]:
    """Preingest methods that expand a container into sources (override `expand`)."""
    out = {}
    for s in _pre_stages(stages):
        base = s.module.Base
        for m in s.methods:
            try:
                cls = load_method_class(s, m)
            except Exception:  # noqa: BLE001 — a broken method shouldn't stop other inputs
                continue
            if cls.expand.__func__ is not base.expand.__func__:
                out[m] = cls
    return out


def _source_tokens(tokens: list[str], stages: list[StageInfo]) -> set[str]:
    """Ingest and preingest method names the user spelled out (bare or stage=…): naming an ingest method
    says "ingest this file itself", which suppresses container expansion; naming a preingest one asks for
    that container type."""
    names = {m for s in stages for m in s.methods if s.name == "ingest" or getattr(s.module, "PRE", False)}
    pre = {s.name for s in _pre_stages(stages)}
    out = set()
    for t in tokens:
        st, sep, m = t.partition("=") if "=" in t else t.partition(":")
        if sep:
            if st == "ingest" or st in pre:
                out.add(m)
        elif t in names:
            out.add(t)
    return out


def expand_source(source: str, tokens: list[str], stages: list[StageInfo]) -> list[BatchItem] | None:
    """Items inside a container source, or None if `source` isn't one (or the user named another ingest
    method for it)."""
    exp = _expanders(stages)
    named = _source_tokens(tokens, stages)
    if named and not named & set(exp):
        return None
    for name, cls in exp.items():
        if (not named or name in named) and cls.accepts(source):
            items = cls.expand(source) or []
            base = Path(source).name
            return [BatchItem(f"{base} [{i}/{len(items)}] {it.title or it.source}", it.source, it.title)
                    for i, it in enumerate(items, 1)]
    return None


def _child_tokens(tokens: list[str], stages: list[StageInfo]) -> list[str]:
    """Method tokens for a container's items: without the preingest method that expanded the container."""
    exp = set(_expanders(stages))
    pre = {s.name for s in _pre_stages(stages)}
    out = []
    for t in tokens:
        st, sep, m = t.partition("=") if "=" in t else t.partition(":")
        if t in exp or (sep and m in exp and st in pre):
            continue
        out.append(t)
    return out


def _plan_str(steps: list[Step]) -> str:
    return " -> ".join(f"{s.stage.name}={s.method}" + ("" if s.why == "cli" else f" ({s.why})") for s in steps)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="incant", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="file, URL, book slug, book work dir, or a directory of files (batch)")
    ap.add_argument("methods", nargs="*", help="method per stage (bare name or stage=method); optional trailing output path")
    ap.add_argument("-c", "--config", type=Path, help="config TOML (default: incant.toml in cwd or $INCANT_HOME)")
    ap.add_argument("-o", "--out", type=Path, help="output file (one book) or output tree base directory")
    ap.add_argument("--mode", choices=PUBLISHING_MODES, default="audiobook", help="publishing pipeline (default: audiobook)")
    ap.add_argument("--slug", help="work-dir name for a new book (default: from title)")
    ap.add_argument("--from", dest="start", metavar="STAGE", help="start at this stage (earlier stages must have run)")
    ap.add_argument("--until", dest="stop", metavar="STAGE", help="stop after this stage")
    ap.add_argument("--force", metavar="STAGES", default="", help="re-run these stages even if up to date (comma list, or 'all')")
    ap.add_argument("--chapters", help="restrict per-chapter stages, e.g. 1-3,7")
    ap.add_argument("--dry-run", action="store_true", help="show the resolved plan and exit")
    ap.add_argument("--list", action="store_true", help="list stages and methods")
    ap.add_argument("--version", action="version", version=f"incant {__version__}")
    ap.add_argument("--licenses", action="store_true", help="known license terms per method, and gaps")
    ap.add_argument("--check", action="store_true", help="check readiness of the resolved methods (endpoints, voices, tools)")
    ap.add_argument("--review", action="store_true", help="serve the review/edit page for an existing book")
    ap.add_argument("--serve", "--daemon", action="store_true", help="run the local pipeline and review dashboard")
    ap.add_argument("--allow-path", action="append", type=Path, default=[],
                    help="extra filesystem root for the dashboard (repeatable, --serve only)")
    ap.add_argument("--llm", metavar="PROFILE", help="active LLM profile (overrides current_llm in config)")
    ap.add_argument("--host", help="review page bind address (default: $INCANT_REVIEW_HOST, else [review] host, 127.0.0.1)")
    ap.add_argument("--port", type=int, help="review page port (default [review] port, 8765)")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config, current_llm=args.llm)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    stages = discover(args.mode)
    if args.list:
        return cmd_list(stages, cfg)
    if args.licenses:
        from .credits import licenses_table

        print(licenses_table(cfg, stages))
        return 0
    if args.serve:
        if (args.input or args.review or args.methods or args.check or args.dry_run or args.out or args.slug
                or args.start or args.stop or args.force or args.chapters is not None or args.mode != "audiobook"):
            ap.error("--serve starts the dashboard and takes no input or pipeline options")
        from .review.daemon import serve_dashboard

        env_host = os.environ.get("INCANT_REVIEW_HOST")
        try:
            serve_dashboard(cfg, args.config, args.llm, args.host or env_host or cfg.review.host,
                            args.port if args.port is not None else cfg.review.port,
                            published=bool(env_host) and not args.host, allow_paths=tuple(args.allow_path))
        except (ValueError, OSError, RuntimeError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0
    if args.allow_path:
        ap.error("--allow-path requires --serve")
    if args.mode == "ebook" and (args.chapters is not None or args.llm or args.review):
        ap.error("Ebook mode uses the dashboard for review and has no chapter or LLM selection")
    try:
        validate_stage_range([s.name for s in stages if not getattr(s.module, "PRE", False)], args.start, args.stop)
        args.chapters = _parse_chapters(args.chapters)
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # INPUT: new source, an existing book (slug or work dir) to resume, or a directory of sources (batch)
    source: str | None = args.input
    try:
        for value, what in ((source, "A book source"), (args.out, "An output file")):
            if value and "://" not in str(value):
                refuse_protected(value, what)
    except ProtectedFileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.check and source and not Path(source).exists() and not (cfg.paths.work_dir / source).exists() \
            and (source.partition("=")[0] in {s.name for s in stages} or any(source in s.methods for s in stages)):
        args.methods, source = [source, *args.methods], None  # `--check characters speakers`: methods, no book
    if source and not source.startswith(("http://", "https://")) and not Path(source).exists() \
            and (cfg.paths.data_dir / source).exists():
        source = str(cfg.paths.data_dir / source)  # "books/x.epub" from anywhere: it's in the data directory
    if source and Path(source).is_dir() and not (Path(source) / "meta.json").exists():
        if args.review:
            print("error: --review needs one existing book, not a directory of sources", file=sys.stderr)
            return 2
        return run_batch(args, cfg, stages, _folder_items(Path(source), args.methods, stages), source)
    if source and Path(source).is_file() and (items := expand_source(source, args.methods, stages)) is not None:
        if args.review:
            print("error: --review needs one existing book, not a file of sources", file=sys.stderr)
            return 2
        return run_batch(args, cfg, stages, items, source, tokens=_child_tokens(args.methods, stages))
    if source and _source_tokens(args.methods, stages) & set(_expanders(stages)):
        print(f"error: '{source}' is not a container file the named method can expand", file=sys.stderr)
        return 2
    wd: WorkDir | None = None
    if source and not source.startswith(("http://", "https://")):
        p = Path(source)
        if p.is_dir() and (p / "meta.json").exists():
            wd = WorkDir(p.resolve())
        elif not p.exists() and (cfg.paths.work_dir / source / "meta.json").exists():
            wd = WorkDir.for_slug(cfg.paths.work_dir, source)
        elif not p.exists():
            print(f"error: input '{source}' is not a file, URL, or known book in {cfg.paths.work_dir}", file=sys.stderr)
            return 2
    if source is None and not args.check:
        ap.print_help()
        return 2
    if args.review:
        if wd is None:
            print("error: --review needs an existing book (slug or work dir), not a new source", file=sys.stderr)
            return 2
        from .review.server import serve

        # INCANT_REVIEW_HOST: set by compose.desktop.yaml, where the page must listen on the container's own
        # interfaces and compose publishes the port on the host's 127.0.0.1 only.
        env_host = os.environ.get("INCANT_REVIEW_HOST")
        local_host = "127.0.0.1" if cfg.review.public_domains else cfg.review.host
        host = args.host or env_host or local_host
        try:
            serve(cfg, wd, host, args.port or cfg.review.port, published=bool(env_host) and not args.host)
        except (ValueError, OSError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0

    try:
        steps, out_path = resolve(args.methods, None if wd else source, cfg, stages,
                                  resuming=wd is not None or (args.check and source is None))
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"plan: {_plan_str(steps)}")

    if args.check:
        return run_checks(cfg, steps)
    if args.dry_run:
        return 0

    target = args.out or out_path
    tree = target is not None and (target.is_dir() or not target.suffix)  # a directory: base of the output tree
    ctx = Context(cfg=cfg, source=None if wd else source, wd=wd, slug=args.slug, out=None if tree else target,
                  out_dir=target if tree else None,
                  chapters=args.chapters)
    force = {f.strip() for f in args.force.split(",") if f.strip()}
    try:
        run_pipeline(steps, ctx, start=args.start, stop=args.stop, force=force)
    except (ResolveError, ProtectedFileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        _print_llm_usage()
    return 0


def _print_llm_usage() -> None:
    from .llm import USAGE

    if USAGE["requests"]:
        print(f"LLM usage: {USAGE['requests']} requests, {USAGE['input']:,} tokens in, {USAGE['output']:,} out")


def _folder_items(folder: Path, tokens: list[str], stages: list[StageInfo]) -> list[BatchItem]:
    """Files in `folder` (not recursive) by name; container files are replaced by their items."""
    items: list[BatchItem] = []
    files = sorted((f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".") and not is_protected_file(f)),
                   key=lambda f: f.name.casefold())
    for f in files:
        inner = expand_source(str(f), tokens, stages)
        items += inner if inner is not None else [BatchItem(f.name, str(f))]
    return items


def run_checks(cfg, steps: list[Step]) -> int:
    """--check: tools on PATH plus each planned method's own readiness checks."""
    import shutil

    from .config import exposed_env_files

    ok = True
    for f in exposed_env_files():
        print(f"WARN {f} holds secrets but other users can read it: chmod 600 {f}")
    audio = any(step.stage.name == "synthesize" for step in steps)
    for tool in (("ffmpeg", "ffprobe") if audio else ()):
        found = shutil.which(tool)
        ok &= bool(found)
        print(f"{'ok ' if found else 'ERR'} {tool}")
    seen = set()
    for st in steps:
        if st.method == "-" or (st.stage.name, st.method) in seen:
            continue
        seen.add((st.stage.name, st.method))
        for good, msg in instantiate(cfg, st.stage, st.method).check():
            ok &= good
            print(f"{'ok ' if good else 'ERR'} [{st.stage.name}.{st.method}] {msg}")
    return 0 if ok else 1


def run_batch(args, cfg, stages, items: list[BatchItem], origin: str, tokens: list[str] | None = None) -> int:
    """Run the pipeline for each item, in lanes (lanes.py): books overlap, one at a time per server. A failing
    item is reported and the batch continues."""
    if args.slug:
        print("error: --slug names one book; a batch names each book from its title", file=sys.stderr)
        return 2
    if tokens is None:
        tokens = _child_tokens(args.methods, stages)
    seen: set[str] = set()
    plans: list[tuple[BatchItem, list[Step], Path | None]] = []
    for it in items:
        if it.source in seen:  # the same page bookmarked twice, or in two bookmark files
            continue
        seen.add(it.source)
        try:
            steps, out_path = resolve(tokens, it.source, cfg, stages)
        except ResolveError as e:
            if "no method accepts" in str(e):
                print(f"skip: {it.label} (not a supported source)")
                continue
            print(f"error: {e}", file=sys.stderr)
            return 2
        plans.append((it, steps, out_path))
    if not plans:
        print(f"error: no supported sources in {origin}", file=sys.stderr)
        return 2
    out_dir = args.out or plans[0][2]
    if out_dir is not None and out_dir.suffix and not out_dir.is_dir():
        print(f"error: a batch writes one file per book — give an output directory, not '{out_dir}'", file=sys.stderr)
        return 2
    force = {x.strip() for x in args.force.split(",") if x.strip()}
    mode = cfg.run.lanes
    methods: dict[tuple[str, str], object] = {}

    def resource_of(step: Step) -> str | None:
        k = (step.stage.name, step.method)
        if k not in methods:
            methods[k] = instantiate(cfg, step.stage, step.method)
        return methods[k].resource()  # type: ignore[attr-defined]

    try:
        phases = [plan_phases(steps, resource_of, mode, args.start, args.stop) for _, steps, _ in plans]
    except Exception as e:  # noqa: BLE001 — a method that can't be set up fails the same way when it runs
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"batch: {len(plans)} books from {origin}")
    lanes_used = {p.lane for ph in phases for p in ph}
    if len(lanes_used) > 1:
        shown = dict.fromkeys(describe(ph) for ph in phases)
        print("lanes (one book at a time in each, books in parallel across them):")
        for d in shown:
            print(f"  {d}")
    if args.check:  # every distinct plan's methods, once
        return run_checks(cfg, [st for _, steps, _ in plans for st in steps])
    if args.dry_run:
        for it, steps, _ in plans:
            print(f"  {it.label}: {_plan_str(steps)}")
        return 0
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    import threading

    out_lock = threading.Lock()

    def say(msg: str, err: bool = False) -> None:
        with out_lock:
            print(msg, file=sys.stderr if err else sys.stdout, flush=True)

    tag = len(lanes_used) > 1  # several books at once: mark each line with its book
    done: list[tuple[int, str, object]] = []
    failed: list[tuple[int, str, str]] = []
    jobs: list[Job] = []
    for n, ((it, steps, _), ph) in enumerate(zip(plans, phases), 1):
        ctx = Context(cfg=cfg, source=it.source, title=it.title, out_dir=out_dir, chapters=args.chapters,
                      log=(lambda msg, n=n: say(f"[{n}] {msg}")) if tag else (lambda msg: say(msg)))

        def run(phase, i, n=n, it=it, steps=steps, ctx=ctx, ph=ph):
            if i == 0 or tag:
                where = f" — {', '.join(phase.stages)} ({phase.lane})" if tag else ""
                say(f"\n=== [{n}/{len(plans)}] {it.label}{where}")
            if i == 0:
                ctx.log(f"plan: {_plan_str(steps)}")
            run_pipeline(steps, ctx, start=phase.first, stop=phase.last, force=force, log_skips=i == 0)

        jobs.append(Job(key=(n, it, ctx), phases=ph, run=run))

    def finish(job: Job) -> None:
        n, it, ctx = job.key
        if job.error is None:
            done.append((n, it.label, ctx.results.get("output") or ctx.results.get("publish") or (ctx.wd.root if ctx.wd else "")))
        else:
            e = job.error
            failed.append((n, it.label, f"{type(e).__name__}: {e}"))
            say(f"FAILED {it.label}: {type(e).__name__}: {e}", err=True)

    run_lanes(jobs, finish)
    print(f"\nbatch finished: {len(done)} ok, {len(failed)} failed")
    _print_llm_usage()
    for _, name, result in sorted(done, key=lambda d: d[0]):  # in batch order, not finishing order
        print(f"  ok    {name} -> {result}")
    for _, name, err in sorted(failed, key=lambda f: f[0]):
        print(f"  FAIL  {name}: {err[:200]}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
