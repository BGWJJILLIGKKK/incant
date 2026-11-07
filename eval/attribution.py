"""Score speaker attribution in a book's spans against a hand-labelled gold file.

    uv run python eval/attribution.py eval/gold/pride-and-prejudice.json [--work work]

Gold labels are character names; they match a character's canonical name or any alias. Special labels:
  "narrator"   quoted words nobody speaks: the narrator, unknown, or (first person) the narrating character
  "~a|b"       any character whose name or alias contains `a` (or `b`) is right
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from incant.config import load_config
from incant.workdir import WorkDir


@dataclass
class Score:
    book: str
    ok: int
    n: int
    errors: list[tuple[str, str, str | None]] = field(default_factory=list)  # (span id, want, got)

    @property
    def accuracy(self) -> float:
        return self.ok / self.n if self.n else 0.0


def gold_chapters(gold: dict) -> list[str]:
    return sorted({k.split("-")[0] for k in gold["spans"]})


def score(gold_path: Path, work: Path | None = None) -> Score:
    gold = json.loads(gold_path.read_text())
    wd = WorkDir.for_slug(work or load_config().paths.work_dir, gold["book"])
    chars = wd.read_characters()
    name_to_id, names_of = {}, {}
    for c in chars.characters if chars else []:
        for n in (c.canonical_name, *c.aliases):
            name_to_id.setdefault(n.casefold(), c.id)
        names_of[c.id] = " ".join([c.canonical_name, *c.aliases]).casefold()
    narrator_char = chars.narrator if chars else None

    pred = {}
    for ch in gold_chapters(gold):
        if wd.spans_path(ch).exists():
            for s in wd.read_spans(ch):
                pred[s.id] = s.speaker
    out = Score(gold["book"], 0, len(gold["spans"]))
    for sid, name in gold["spans"].items():
        got = pred.get(sid)
        if name == "narrator":  # in a first-person book the narrator is that character (same voice)
            want, hit = "narrator", got in (None, "narrator") or (bool(got) and got == narrator_char)
        elif name.startswith("~"):
            want = name
            hit = bool(got) and any(w.strip().casefold() in names_of.get(got, "") for w in name[1:].split("|"))
        else:
            want = name_to_id.get(name.casefold(), f"?{name}")
            hit = got == want
        if hit:
            out.ok += 1
        else:
            out.errors.append((sid, want, got))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("gold", type=Path)
    ap.add_argument("--work", type=Path)
    args = ap.parse_args()
    s = score(args.gold, args.work)
    print(f"accuracy {s.ok}/{s.n} = {s.accuracy:.1%}")
    for (want, got), k in Counter((w, g) for _, w, g in s.errors).most_common():
        print(f"  {k:>3}x  {want} -> {got}")
    for sid, want, got in s.errors:
        print(f"  {sid}: want {want}, got {got}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
