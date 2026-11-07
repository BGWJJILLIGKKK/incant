# incant

Books in (txt, epub, URL), fully-voiced multi-speaker m4b audiobooks out — with a
local LLM for character analysis and speaker attribution, and IndexTTS for voices.

## Usage

The pipeline is spelled out on the command line, one method per stage:

```sh
./incant book.epub epub characters speakers rules indextts m4b   # fully explicit
./incant book.epub                                     # everything from [pipeline] defaults / auto-detect
./incant book.epub dummy out/test.m4b                  # override one stage; trailing path = output file
./incant book.epub synthesize=indextts                 # stage=method when a name is ambiguous
./incant pride-and-prejudice --from synthesize         # resume a book already in work/
./incant --list                                        # stages and their methods
./incant book.epub --check                             # endpoints, voices, ffmpeg for the resolved plan
```

Other options: `--until STAGE`, `--force STAGE[,..]|all`, `--chapters 1-3,7`,
`--slug NAME`, `--dry-run`, `-c CONFIG`.

**Resolution:** bare names unique to one stage go to that stage wherever they
appear; names shared by several stages go to the next matching stage in order;
stages not named fall back to `[pipeline]` in `incant.toml`, then auto-detection
(ingest), then the stage's only method.

**Re-runs:** parse and attribute are skipped when their method, settings and
upstream outputs are unchanged, so hand edits to their outputs survive. Synthesis
is content-cached per segment, so it always runs but only renders what changed.

## Stages

| # | Stage | Writes (in `work/<slug>/`) | Methods |
|---|---|---|---|
| 1 | `ingest` | `meta.json`, `text.md`, `cover.*` | `epub`, `txt`, `url` |
| 2 | `parse` | `chapters.json`, `chapters/*.txt`, `characters.json` | `basic`, `characters` (LLM) |
| 3 | `attribute` | `spans/<chapter>.jsonl` | `narrator`, `speakers` (LLM) |
| 4 | `normalize` | `speech/<chapter>.json` (spans + TTS-ready `say`), `speech/changes.txt` | `rules`, `none` |
| 5 | `synthesize` | `cast.json`, `audio/chapters/*.wav`, `audio/manifest.json`, `audio/qa_report.json` | `indextts`, `dummy` |
| 6 | `output` | `out/<slug>.m4b` | `m4b` |

**Normalisation** (`rules`) spells out what a TTS is likely to misread — Roman numerals in titles,
section words and regnal names ("Henry VIII"), years/numbers/money/times, abbreviations ("Mr.", "St."),
ALL-CAPS runs, symbols, archaic hyphenation — conservatively, leaving the pronoun "I" and real acronyms
alone. Every change is listed in `speech/changes.txt`. Pronunciations go in `lexicon.toml` (project) or
`work/<book>/lexicon.toml` as `[words] Name = "respelling"`; book text itself is never changed.

## Adding a method

Drop a directory into the stage:

```
src/incant/stages/<stage>/<name>/__init__.py   # from .<name> import *
src/incant/stages/<stage>/<name>/<name>.py     # one subclass of the stage's Base
```

It is discovered by name — no registration. Each stage's `base.py` defines the
interface (`run(ctx)` plus stage hooks, e.g. `accepts`/`load` for ingest,
`identity`/`synthesize` for TTS). A method's `Settings` (pydantic) are read from
`[<stage>.<name>]` in the config. Plain `.py` files in a stage directory are
shared helpers, not methods. A new stage is a new directory with `ORDER`,
`DESCRIPTION` and `Base` in its `__init__.py`.

## Design rules

- **Stages communicate only through files** in the work dir (schemas in
  `src/incant/schemas.py`). Any stage can be re-run; any file can be hand-edited.
- **The LLM never writes book text.** Spans are cut deterministically; the LLM only
  labels span ids. Splitting is checked to reproduce the chapter text exactly.
- **Attribution ≠ casting.** Spans carry character ids; `cast.json` maps characters
  to voices, so recasting never re-runs the LLM.
- **Audio stays lossless until the output stage.** One lossy encode, at the end.
- **Per-chapter files**, so any chapter can be re-run, resumed, or rendered alone (`--chapters`).
- **One GPU, swapped.** The LLM and IndexTTS share the GPU via llama-swap: LLM stages
  finish the whole book before synthesis starts, so each model is swapped in once.

## Quality checks

- `eval/attribution.py eval/gold/<book>.json` scores speaker attribution against hand labels
  (qwen3.5-9b: Pride and Prejudice ch. I–II 93.8%; Sherlock Holmes "A Scandal in Bohemia" opening 91.7%).
- Every rendered segment is transcribed (faster-whisper, CPU) and compared with its text; mismatches are
  re-rendered and the best take kept. Leftovers are listed in `audio/qa_report.json`.

## Setup

```sh
uv sync --extra qa
./incant --check
uv run pytest
```

`./incant` is a small Python launcher (stdlib only) that runs the package through `uv run` from any
directory; `INCANT_NO_SYNC=1` skips uv's dependency check and uses `.venv` directly.

Config: `incant.toml`, overridden by `incant.local.toml`. Voice library format:
`voices/README.md`.
