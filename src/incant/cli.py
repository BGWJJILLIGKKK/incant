"""./incant INPUT [METHOD ...] [OUTPUT] [options]

The pipeline is spelled out by method names, one per stage, in stage order:

    ./incant book.epub epub basic narrator indextts m4b
    ./incant book.epub indextts                 # other stages from [pipeline] / auto-detect
    ./incant book.epub dummy out/test.m4b       # trailing file path = output location
    ./incant pride-and-prejudice synthesize=indextts --from synthesize   # resume an ingested book
    ./incant pride-and-prejudice --review --host 0.0.0.0                  # review/edit page

INPUT is a file, a URL, an existing book slug under work/, or a book work directory. A directory of
source files (no meta.json in it) is a batch: every supported file in it, by name, one after another,
each through the whole pipeline; a failing book is reported and the batch continues.

    ./incant books/                            # every epub/pdf/txt in books/
    ./incant books/ dummy --until synthesize   # same methods/options for each
    ./incant books/ -o audiobooks/             # outputs collected in one directory
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

from .config import load_config
from .pipeline import ResolveError, StageInfo, Step, discover, instantiate, load_method_class, resolve, run_pipeline
from .stages.base import Context
from .workdir import WorkDir


def _parse_chapters(spec: str | None) -> set[str] | None:
    if not spec:
        return None
    out: set[str] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(f"{n:04d}" for n in range(int(a), int(b or a) + 1))
    return out


def _doc(cls) -> str:
    own = cls.__dict__.get("__doc__")  # not inherited from the stage base
    d = inspect.cleandoc(own) if own else (inspect.getdoc(sys.modules[cls.__module__]) or "")
    return d.splitlines()[0] if d else ""


def cmd_list(stages: list[StageInfo], cfg) -> int:
    for s in stages:
        default = cfg.pipeline.get(s.name)
        print(f"{s.order}. {s.name:<11} {s.description}")
        for m in s.methods:
            try:
                doc = _doc(load_method_class(s, m))
            except Exception as e:  # noqa: BLE001
                doc = f"(failed to load: {e})"
            mark = "*" if m == default else " "
            print(f"   {mark} {m:<10} {doc}")
        if default == "auto":
            print("   * auto       (detected from the input)")
    print("\n* = default from [pipeline] in config")
    return 0


def _plan_str(steps: list[Step]) -> str:
    return " -> ".join(f"{s.stage.name}={s.method}" + ("" if s.why == "cli" else f" ({s.why})") for s in steps)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="incant", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="file, URL, book slug, book work dir, or a directory of files (batch)")
    ap.add_argument("methods", nargs="*", help="method per stage (bare name or stage=method); optional trailing output path")
    ap.add_argument("-c", "--config", type=Path, help="config TOML (default: incant.toml in cwd or $INCANT_HOME)")
    ap.add_argument("-o", "--out", type=Path, help="final output path")
    ap.add_argument("--slug", help="work-dir name for a new book (default: from title)")
    ap.add_argument("--from", dest="start", metavar="STAGE", help="start at this stage (earlier stages must have run)")
    ap.add_argument("--until", dest="stop", metavar="STAGE", help="stop after this stage")
    ap.add_argument("--force", metavar="STAGES", default="", help="re-run these stages even if up to date (comma list, or 'all')")
    ap.add_argument("--chapters", help="restrict per-chapter stages, e.g. 1-3,7")
    ap.add_argument("--dry-run", action="store_true", help="show the resolved plan and exit")
    ap.add_argument("--list", action="store_true", help="list stages and methods")
    ap.add_argument("--check", action="store_true", help="check readiness of the resolved methods (endpoints, voices, tools)")
    ap.add_argument("--review", action="store_true", help="serve the review/edit page for an existing book")
    ap.add_argument("--host", help="review page bind address (default [review] host, 127.0.0.1)")
    ap.add_argument("--port", type=int, help="review page port (default [review] port, 8765)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    stages = discover()
    if args.list:
        return cmd_list(stages, cfg)

    # INPUT: new source, an existing book (slug or work dir) to resume, or a directory of sources (batch)
    source: str | None = args.input
    if source and Path(source).is_dir() and not (Path(source) / "meta.json").exists():
        if args.review:
            print("error: --review needs one existing book, not a directory of sources", file=sys.stderr)
            return 2
        return run_batch(args, cfg, stages, Path(source))
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

        serve(cfg, wd, args.host or cfg.review.host, args.port or cfg.review.port)
        return 0

    try:
        steps, out_path = resolve(args.methods, None if wd else source, cfg, stages,
                                  resuming=wd is not None or (args.check and source is None))
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"plan: {_plan_str(steps)}")

    if args.check:
        import shutil

        ok = True
        for tool in ("ffmpeg", "ffprobe"):
            found = shutil.which(tool)
            ok &= bool(found)
            print(f"{'ok ' if found else 'ERR'} {tool}")
        for st in steps:
            if st.method == "-":
                continue
            for good, msg in instantiate(cfg, st.stage, st.method).check():
                ok &= good
                print(f"{'ok ' if good else 'ERR'} [{st.stage.name}.{st.method}] {msg}")
        return 0 if ok else 1
    if args.dry_run:
        return 0

    ctx = Context(cfg=cfg, source=None if wd else source, wd=wd, slug=args.slug, out=args.out or out_path,
                  chapters=_parse_chapters(args.chapters))
    force = {f.strip() for f in args.force.split(",") if f.strip()}
    try:
        run_pipeline(steps, ctx, start=args.start, stop=args.stop, force=force)
    except ResolveError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


def run_batch(args, cfg, stages, folder: Path) -> int:
    """Run the pipeline for every supported file in `folder` (not recursive), sequentially."""
    if args.slug:
        print("error: --slug names one book; a batch names each book from its title", file=sys.stderr)
        return 2
    files = sorted((f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")), key=lambda f: f.name.casefold())
    plans: list[tuple[Path, list[Step], Path | None]] = []
    for f in files:
        try:
            steps, out_path = resolve(args.methods, str(f), cfg, stages)
        except ResolveError as e:
            if "no method accepts" in str(e):
                print(f"skip: {f.name} (not a supported source)")
                continue
            print(f"error: {e}", file=sys.stderr)
            return 2
        plans.append((f, steps, out_path))
    if not plans:
        print(f"error: no supported source files in {folder}", file=sys.stderr)
        return 2
    out_dir = args.out or plans[0][2]
    if out_dir is not None and out_dir.suffix and not out_dir.is_dir():
        print(f"error: a batch writes one file per book — give an output directory, not '{out_dir}'", file=sys.stderr)
        return 2
    print(f"batch: {len(plans)} books from {folder}")
    if args.dry_run:
        for f, steps, _ in plans:
            print(f"  {f.name}: {_plan_str(steps)}")
        return 0
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    force = {x.strip() for x in args.force.split(",") if x.strip()}
    done: list[tuple[str, object]] = []
    failed: list[tuple[str, str]] = []
    for n, (f, steps, _) in enumerate(plans, 1):
        print(f"\n=== [{n}/{len(plans)}] {f.name}", flush=True)
        print(f"plan: {_plan_str(steps)}", flush=True)
        ctx = Context(cfg=cfg, source=str(f), out_dir=out_dir, chapters=_parse_chapters(args.chapters))
        try:
            run_pipeline(steps, ctx, start=args.start, stop=args.stop, force=force)
            done.append((f.name, ctx.results.get("output") or (ctx.wd.root if ctx.wd else "")))
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — one bad book shouldn't stop the batch
            failed.append((f.name, f"{type(e).__name__}: {e}"))
            print(f"FAILED {f.name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    print(f"\nbatch finished: {len(done)} ok, {len(failed)} failed")
    for name, result in done:
        print(f"  ok    {name} -> {result}")
    for name, err in failed:
        print(f"  FAIL  {name}: {err[:200]}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
