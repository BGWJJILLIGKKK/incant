"""Score any parse x attribute combination on the gold books, in scratch copies (the data directory is never
written).

    uv run python eval/compare.py --parse keep --attribute tags      # keep each book's characters.json (LLM)
    uv run python eval/compare.py --parse basic --attribute tags --books dracula,tom-sawyer

Each gold book's meta.json and text.md are copied to a temporary data directory; `parse` runs there (unless
`keep`), then `attribute` on the gold chapters. Two scores per book:
  strict  eval/attribution.py: the gold name must match the character list (as the main suite scores)
  loose   for self-built lists: a gold name matches a character sharing its name, an alias or its last
          distinctive word (Ebenezer Scrooge ~ Scrooge); first-person narrator lines in the narrator voice count
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from attribution import gold_chapters, score  # noqa: E402

from incant.config import load_config  # noqa: E402
from incant.workdir import WorkDir  # noqa: E402

HON = {"mr", "mrs", "miss", "ms", "dr", "sir", "lady", "lord", "the", "captain", "aunt", "uncle", "master", "madam", "count"}


def _toks(n: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", n.lower().replace("’", "'").split("'s")[0]) if t not in HON and len(t) > 1}


def loose(gold: dict, wd: WorkDir, real: WorkDir) -> tuple[int, int]:
    chars = wd.read_characters()
    names = {c.id: [c.canonical_name, *c.aliases] for c in chars.characters} if chars else {}
    rc = real.read_characters()
    rn = next((c for c in rc.characters if c.id == rc.narrator), None) if rc else None
    narr_names = {n.lower() for n in ([rn.canonical_name, *rn.aliases] if rn else [])}
    pred = {}
    for ch in gold_chapters(gold):
        if wd.spans_path(ch).exists():
            pred.update({s.id: s.speaker for s in wd.read_spans(ch)})
    ok = 0
    for sid, name in gold["spans"].items():
        got = pred.get(sid)
        if name == "narrator":
            hit = got in (None, "narrator") or bool(chars and got == chars.narrator)
        elif got == "narrator" and chars and chars.narrator == "narrator" and (
                name.lower() in narr_names or any(w.strip() in " ".join(narr_names) for w in name.lstrip("~").lower().split("|"))):
            hit = True
        elif not got or got not in names:
            hit = False
        elif name.startswith("~"):
            hit = any(w.strip().lower() in " ".join(names[got]).lower() for w in name[1:].split("|"))
        else:
            gn = name.lower()
            hit = any(gn == n.lower() for n in names[got]) or any(
                (_toks(name) & _toks(n)) and (not ({"mr", "mrs", "miss"} & set(gn.split(". ")[0:1]))
                                              or n.lower().split(".")[0] == gn.split(".")[0]) for n in names[got])
        ok += hit
    return ok, len(gold["spans"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse", default="keep", help="keep (copy characters.json) | basic | characters")
    ap.add_argument("--attribute", default="tags")
    ap.add_argument("--books", help="comma-separated subset of eval/gold")
    ap.add_argument("--set", action="append", default=[], metavar="TOML",
                    help="extra config, e.g. --set '[parse.characters]\nwindow_words = 3000'")
    ap.add_argument("--keep-dir", type=Path, help="keep the scratch copy here (default: a temp dir, removed)")
    args = ap.parse_args()
    cfg = load_config()
    golds = sorted((ROOT / "eval" / "gold").glob("*.json"))
    if args.books:
        golds = [g for g in golds if g.stem in set(args.books.split(","))]
    tmp = args.keep_dir or Path(tempfile.mkdtemp(prefix="incant-compare-"))
    if tmp.exists() and args.keep_dir:
        shutil.rmtree(tmp)
    (tmp / "work").mkdir(parents=True)
    pipeline = {"parse": args.parse if args.parse != "keep" else "basic", "attribute": args.attribute}
    (tmp / "incant.toml").write_text('[paths]\ndata_dir = "."\nwork_dir = "work"\n[pipeline]\n'
                                        + "".join(f'{k} = "{v}"\n' for k, v in pipeline.items())
                                        + "normalize = \"rules\"\nsynthesize = \"dummy\"\n"
                                        + "".join(f"{s}\n" for s in args.set))
    rows, tot = [], [0, 0, 0]
    for g in golds:
        gold = json.loads(g.read_text())
        book = gold["book"]
        real = WorkDir.for_slug(cfg.paths.work_dir, book)
        if not (real.root / "text.md").exists():  # not in your work directory: the copy shipped in eval/books/
            real = WorkDir(ROOT / "eval" / "books" / book)
        dst = tmp / "work" / book
        dst.mkdir()
        for f in ("meta.json", "text.md"):
            shutil.copy(real.root / f, dst / f)
        start = "parse"
        if args.parse == "keep":
            for f in ("chapters.json", "characters.json"):
                shutil.copy(real.root / f, dst / f)
            shutil.copytree(real.root / "chapters", dst / "chapters")
            start = "attribute"
        spec = ",".join(str(int(c)) for c in gold_chapters(gold))
        cmd = [sys.executable, "-m", "incant", book, "--from", start, "--until", "attribute", "--force", "all",
               "--chapters", spec, "-c", str(tmp / "incant.toml")]
        r = subprocess.run(cmd, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
        if r.returncode:
            rows.append(f"{book:36} FAILED: {(r.stderr or r.stdout).strip().splitlines()[-1][:120]}")
            continue
        s = score(g, tmp / "work")
        lo, n = loose(gold, WorkDir(dst), real)
        tot = [tot[0] + s.ok, tot[1] + lo, tot[2] + n]
        rows.append(f"{book:36} strict {s.ok:3}/{n:<3} {s.ok / n:6.1%}   loose {lo:3}/{n:<3} {lo / n:6.1%}")
    print(f"parse={args.parse} attribute={args.attribute}")
    print("\n".join(rows))
    if tot[2]:
        print(f"{'TOTAL':36} strict {tot[0]:3}/{tot[2]:<3} {tot[0] / tot[2]:6.1%}   loose {tot[1]:3}/{tot[2]:<3} {tot[1] / tot[2]:6.1%}")
    if not args.keep_dir:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
