"""Print a chapter with quotation ids marked, for hand-labelling gold sets (no model predictions shown).

    uv run python eval/show.py <book> <chapter id> [max quotes]
"""
import sys

from incant.config import load_config
from incant.workdir import WorkDir

book, cid = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 60
wd = WorkDir.for_slug(load_config().paths.work_dir, book)
spans = wd.read_spans(cid)
paras: dict[int, list] = {}
for s in spans:
    paras.setdefault(s.para, []).append(s)
n = 0
for p, ss in paras.items():
    if n >= limit:
        break
    out = []
    for s in ss:
        if s.kind == "dialogue":
            n += 1
            out.append(f"[{int(s.id.split('-')[1])}]{s.text}")
        else:
            out.append(s.text)
    print("".join(out).replace("*", ""), end="\n\n")
