"""A/B: Kokoro with espeak vs Misaki pronunciation, scored by speech-to-text (word error rate) on the same text.

    uv run python eval/g2p_ab.py --out /tmp/g2p-ab [--n 40] [--books a,b,c]

Segments: TTS-ready text from books already normalized in the data directory, plus sentences full of names
and heteronyms. Writes <out>/<mode>/<i>.wav for listening and prints mean WER, failing segments and the
segments where the two modes differ most. Heteronyms (read/read, lead/lead) are spelled the same whichever way
they're said, so WER can't judge them: listen to the paired files for those.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from incant.config import load_config
from incant.pipeline import discover, instantiate
from incant.qa import Transcriber

EXTRA = [
    "I read the letter yesterday, and now I read it again.",
    "The lead singer dropped a lead pipe on the stage.",
    "She shed a tear when she saw the tear in her dress.",
    "They will record a new record in the old studio.",
    "Mr. Darcy bowed to Elizabeth and Mrs. Bennet.",
    "Scrooge, Marley and the Fezziwigs kept Christmas in their own ways.",
    "Count Dracula welcomed Jonathan Harker to the castle in Transylvania.",
    "Sherlock Holmes and Doctor Watson took a hansom to Baker Street.",
    "Hermione wound the bandage around the wound.",
    "The wind was too strong to wind the sail.",
]


def segments(cfg, books: list[str], n: int) -> list[str]:
    out = []
    for b in books:
        for f in sorted((cfg.paths.work_dir / b / "speech").glob("*.json"))[:3]:
            for s in json.loads(f.read_text())["spans"]:
                t = (s.get("say") or "").strip()
                if 40 <= len(t) <= 280:
                    out.append(t)
    random.Random(7).shuffle(out)
    return out[:n] + EXTRA


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--books", default="pride-and-prejudice,a-christmas-carol")
    args = ap.parse_args()
    cfg = load_config()
    texts = segments(cfg, args.books.split(","), args.n)
    stage = {s.name: s for s in discover()}["synthesize"]
    qa = Transcriber("base.en", 8)
    voices = ["af_heart", "bm_george", "am_michael", "bf_emma"]
    results: dict[str, list[float]] = {}
    for mode in ("espeak", "misaki"):
        m = instantiate(cfg, stage, "kokoro")
        m.settings.g2p = mode
        lib = m.voices().voices
        (args.out / mode).mkdir(parents=True, exist_ok=True)
        wers = []
        for i, t in enumerate(texts):
            wav = m.synthesize(t, lib[voices[i % len(voices)]], None)
            (args.out / mode / f"{i:03d}.wav").write_bytes(wav)
            wers.append(qa.check(wav, t).wer)
        results[mode] = wers
        print(f"{mode:7} mean WER {sum(wers) / len(wers):.3f}, segments over 0.25: {sum(w > 0.25 for w in wers)}"
              f"/{len(wers)}", flush=True)
    (args.out / "texts.json").write_text(json.dumps(texts, indent=1))
    diffs = sorted(range(len(texts)), key=lambda i: results["misaki"][i] - results["espeak"][i])
    print("\nmisaki better:", [(i, round(results['espeak'][i], 2), round(results['misaki'][i], 2), texts[i][:50]) for i in diffs[:4]])
    print("espeak better:", [(i, round(results['espeak'][i], 2), round(results['misaki'][i], 2), texts[i][:50]) for i in diffs[-4:]])
    print(f"\nheteronym/name sentences: {len(texts) - len(EXTRA)}..{len(texts) - 1} in {args.out}/<mode>/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
