"""Attribution eval suite: every gold set in eval/gold/, compared with a saved baseline.

    uv run python eval/suite.py                    # re-attribute each book's gold chapters, score, compare
    uv run python eval/suite.py --no-run           # score what's on disk
    uv run python eval/suite.py --books dracula,tom-sawyer
    uv run python eval/suite.py --save-baseline    # make this run the baseline

Re-attribution touches only the gold chapters (--chapters), with the configured attribute method and the
book's current characters.json. A book dropping by REGRESSION_QUOTES or more vs the baseline is flagged;
gold sets are ~30-60 quotes, so a single quote either way is noise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from attribution import gold_chapters, score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "eval" / "gold"
BASELINE = ROOT / "eval" / "baseline.json"
RESULTS = ROOT / "eval" / "results"
REGRESSION_QUOTES = 2


def attribute(book: str, chapters: list[str], log: Path) -> bool:
    spec = ",".join(str(int(c)) for c in chapters)
    cmd = [sys.executable, "-m", "incant", book, "--from", "attribute", "--until", "attribute",
           "--force", "attribute", "--chapters", spec]
    with open(log, "w") as f:
        return subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-run", action="store_true", help="score existing spans without re-attributing")
    ap.add_argument("--books", help="comma-separated subset")
    ap.add_argument("--save-baseline", action="store_true")
    args = ap.parse_args()

    golds = sorted(GOLD.glob("*.json"))
    if args.books:
        want = set(args.books.split(","))
        golds = [g for g in golds if g.stem in want]
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    logs = ROOT / "logs" / "suite"
    logs.mkdir(parents=True, exist_ok=True)

    rows, results = [], {}
    for g in golds:
        gold = json.loads(g.read_text())
        book = gold["book"]
        if not (ROOT / "work" / book / "characters.json").exists():
            rows.append((book, None, "no characters.json (run parse=characters)"))
            continue
        t = time.time()
        if not args.no_run and not attribute(book, gold_chapters(gold), logs / f"{book}.log"):
            rows.append((book, None, f"attribution failed — see logs/suite/{book}.log"))
            continue
        s = score(g)
        results[book] = {"ok": s.ok, "n": s.n}
        rows.append((book, s, f"{time.time() - t:.0f}s"))
        print(f"  {book}: {s.ok}/{s.n}", flush=True)

    print(f"\n{'book':<36}{'quotes':>7}{'acc':>8}{'base':>8}{'Δ quotes':>10}")
    regressions, tot_ok, tot_n, accs = [], 0, 0, []
    for book, s, note in rows:
        if s is None:
            print(f"{book:<36}{'':>7}{'—':>8}   {note}")
            continue
        b = baseline.get(book)
        delta = s.ok - b["ok"] if b and b["n"] == s.n else None
        flag = ""
        if delta is not None and delta <= -REGRESSION_QUOTES:
            flag = "  REGRESSION"
            regressions.append(book)
        base = f"{b['ok'] / b['n']:.1%}" if b else "—"
        print(f"{book:<36}{s.n:>7}{s.accuracy:>8.1%}{base:>8}{(f'{delta:+d}' if delta is not None else '—'):>10}{flag}")
        tot_ok, tot_n = tot_ok + s.ok, tot_n + s.n
        accs.append(s.accuracy)
    if tot_n:
        b_ok = sum(baseline[b]["ok"] for b in results if b in baseline and baseline[b]["n"] == results[b]["n"])
        b_n = sum(baseline[b]["n"] for b in results if b in baseline and baseline[b]["n"] == results[b]["n"])
        base_all = f"{b_ok / b_n:.1%}" if b_n else "—"
        print(f"{'ALL (pooled)':<36}{tot_n:>7}{tot_ok / tot_n:>8.1%}{base_all:>8}")
        print(f"{'mean of books':<36}{'':>7}{sum(accs) / len(accs):>8.1%}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS / f"{stamp}.json").write_text(json.dumps(results, indent=1) + "\n")
    if args.save_baseline:
        BASELINE.write_text(json.dumps(results, indent=1) + "\n")
        print(f"\nbaseline saved ({len(results)} books)")
    if regressions:
        print(f"\nREGRESSIONS: {', '.join(regressions)}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
