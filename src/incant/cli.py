"""./incant INPUT [METHOD ...] [OUTPUT] [options]

The pipeline is spelled out by method names, one per stage, in stage order:

    ./incant book.epub epub basic narrator indextts m4b
    ./incant book.epub indextts                 # other stages from [pipeline] / auto-detect
    ./incant book.epub dummy out/test.m4b       # trailing file path = output location
    ./incant pride-and-prejudice synthesize=indextts --from synthesize   # resume an ingested book
    ./incant pride-and-prejudice --review --host 0.0.0.0                  # review/edit page

INPUT is a file, a URL, an existing book slug under work/, or a book work directory.
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
    ap.add_argument("input", nargs="?", help="file, URL, book slug, or book work dir")
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

    # INPUT: new source, or an existing book (slug or work dir) to resume
    source: str | None = args.input
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


if __name__ == "__main__":
    raise SystemExit(main())
