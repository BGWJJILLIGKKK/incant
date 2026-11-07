"""Score speaker attribution in a book's spans against a hand-labelled gold file.

    uv run python eval/attribution.py eval/gold/pride-and-prejudice.json [--work work]

Gold labels are character names; they match a character's canonical name or any alias. Special labels:
  "narrator"   quoted words nobody speaks: the narrator or unknown (both use the narrator's voice) is right
  "~word"      an unnamed role: any character whose name or alias contains `word` is right
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from incant.config import load_config
from incant.workdir import WorkDir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", type=Path)
    ap.add_argument("--work", type=Path)
    args = ap.parse_args()
    gold = json.loads(args.gold.read_text())
    wd = WorkDir.for_slug(args.work or load_config().paths.work_dir, gold["book"])
    chars = wd.read_characters()
    name_to_id = {}
    for c in chars.characters if chars else []:
        for n in (c.canonical_name, *c.aliases):
            name_to_id.setdefault(n.casefold(), c.id)

    pred = {}
    for ch in sorted({k.split("-")[0] for k in gold["spans"]}):
        for s in wd.read_spans(ch):
            pred[s.id] = s.speaker
    names_of = {}
    for c in chars.characters if chars else []:
        names_of[c.id] = " ".join([c.canonical_name, *c.aliases]).casefold()
    ok, errors, confusion = 0, [], Counter()
    for sid, name in gold["spans"].items():
        got = pred.get(sid)
        if name == "narrator":
            want, hit = "narrator", got in (None, "narrator")
        elif name.startswith("~"):
            want, hit = name, bool(got) and name[1:].casefold() in names_of.get(got, "")
        else:
            want = name_to_id.get(name.casefold(), f"?{name}")
            hit = got == want
        if hit:
            ok += 1
        else:
            errors.append((sid, want, got))
            confusion[(want, got)] += 1
    n = len(gold["spans"])
    print(f"accuracy {ok}/{n} = {ok / n:.1%}")
    for (want, got), k in confusion.most_common():
        print(f"  {k:>3}x  {want} -> {got}")
    for sid, want, got in errors:
        print(f"  {sid}: want {want}, got {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
