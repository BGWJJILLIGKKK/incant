# incant

Books in (txt, epub, pdf, URL), fully-voiced multi-speaker audiobooks out (m4b, mp3, or a file per chapter).
Every stage has a cheap option and a better one: rule-based or LLM speaker attribution (any OpenAI-compatible
server), and Kokoro on the CPU, any OpenAI-style speech server, or IndexTTS for voices.

**Cheapest full run (no GPU, no servers):** `./incant book.epub basic tags kokoro` — rule-based attribution
and local Kokoro voices.

## Usage

The pipeline is spelled out on the command line, one method per stage:

```sh
./incant book.epub epub characters speakers rules indextts m4b   # fully explicit
./incant book.epub                                     # everything from [pipeline] defaults / auto-detect
./incant book.epub dummy out/test.m4b                  # override one stage; trailing path = output file
./incant book.epub basic tags kokoro mp3               # no LLM, CPU voices, one chaptered mp3
./incant book.epub synthesize=indextts                 # stage=method when a name is ambiguous
./incant pride-and-prejudice --from synthesize         # resume a book already in work/
./incant books/ -o audiobooks/                         # batch: every epub/pdf/txt in books/, one by one
./incant bookmarks.html --until attribute              # batch: every page in a browser bookmark export
./incant --list                                        # stages and their methods
./incant book.epub --check                             # endpoints, voices, ffmpeg for the resolved plan
```

Other options: `--until STAGE`, `--force STAGE[,..]|all`, `--chapters 1-3,7`,
`--slug NAME`, `--dry-run`, `-c CONFIG`.

**Resolution:** bare names unique to one stage go to that stage wherever they
appear; names shared by several stages go to the next matching stage in order;
stages not named fall back to `[pipeline]` in `incant.toml`, then auto-detection
(ingest), then the stage's only method, then its built-in default (basic, narrator,
rules, kokoro, m4b). A trailing `out.mp3` picks the output method by extension.

**Re-runs:** parse and attribute are skipped when their method, settings and
upstream outputs are unchanged, so hand edits to their outputs survive. Synthesis
is content-cached per segment, so it always runs but only renders what changed.

## Stages

| # | Stage | Writes (in `work/<slug>/`) | Methods |
|---|---|---|---|
| 1 | `ingest` | `meta.json`, `text.md`, `cover.*` | `epub`, `pdf`, `txt`, `url` |
| 2 | `parse` | `chapters.json`, `chapters/*.txt`, `characters.json` | `basic`, `characters` (LLM) |
| 3 | `attribute` | `spans/<chapter>.jsonl` | `narrator`, `tags` (rules), `speakers` (LLM) |
| 4 | `normalize` | `speech/<chapter>.json` (spans + TTS-ready `say`), `speech/changes.txt` | `rules`, `none` |
| 5 | `synthesize` | `cast.json`, `audio/chapters/*.wav`, `audio/manifest.json`, `audio/qa_report.json` | `kokoro` (local), `openai` (HTTP), `indextts`, `dummy` |
| 6 | `output` | `output/<author>/<title>/…` (outside `work/`), `output.json` | `m4b`, `mp3`, `chapters` |

**Choosing methods** (speaker accuracy on the 10-book gold suite; see `docs/interfaces.md`):

| Need | parse | attribute | synthesize |
|---|---|---|---|
| Best | `characters` (LLM) | `speakers` (LLM, 95%) | `indextts` (GPU server, emotion) |
| Fewer LLM calls | `characters` (LLM) | `tags` (80%) | `kokoro` |
| No LLM at all | `basic` | `tags` (~68%) | `kokoro` (CPU, ~3–5× real time) |

The LLM can be any OpenAI-compatible server (`[llm] dialect = "openai"` for Ollama, LM Studio, OpenRouter,
OpenAI). Each TTS backend has its own voices and its own cast: switching backends keeps the other's `cast.json`
as `cast.<backend>.json` and brings it back when you switch again.

**Author's notes** on web stories — introductions, commentary, "Thanks for reading!", calls to comment or tip —
are removed from the start and end of each chapter by default (`[parse.<method>] metatext`: `auto` = web pages only,
`rules`, `llm`, `off`); everything removed is listed in `work/<book>/removed.txt`.

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

## Review and edit

```sh
./incant pride-and-prejudice --review                 # http://localhost:8765/
./incant pride-and-prejudice --review --host 0.0.0.0  # reachable from other machines (no auth)
```

- **Script**: the chapter with every quotation coloured by speaker; click any line to change its
  speaker, delivery (emotion + strength) or exact spoken text, and play its rendered segment.
  "Next unknown" walks the unattributed quotes.
- **Cast**: voice per speaking character (pin a voice or leave it automatic), gender/age, voice samples.
- **Pronunciations**: the book's lexicon. **QA**: segments that failed the speech-to-text check.

Edits are written where re-runs keep them: locked spans (attribution keeps them), `say_overrides.json`
(normalize applies them), manual cast roles, locked characters, `work/<book>/lexicon.toml`. The page never
renders audio; re-render a chapter with `./incant <book> --from normalize --chapters N`.

## Quality checks

- `eval/suite.py` scores speaker attribution on ten books' hand-labelled scenes (~500 quotes) against
  `eval/baseline.json` and flags any book that drops by 2+ quotes. Currently 94.8% pooled (qwen3.5-9b);
  per-book numbers in docs/findings.md. Run it before committing any profiling/attribution change.
- Every rendered segment is transcribed (faster-whisper, CPU) and compared with its text; mismatches are
  re-rendered and the best take kept. Leftovers are listed in `audio/qa_report.json`.

## Setup

```sh
uv sync --extra qa --extra browser --extra kokoro   # kokoro: local CPU voices (model downloads on first use)
.venv/bin/playwright install chromium   # headless browser for web pages that load their text with JavaScript
./incant --check
uv run pytest
```

`./incant` is a small Python launcher (stdlib only) that runs the package through `uv run` from any
directory; `INCANT_NO_SYNC=1` skips uv's dependency check and uses `.venv` directly.

Config: `incant.toml`, overridden by `incant.local.toml`. Logins for web pages behind a paywall or
sign-in go in the local file, under `[ingest.url.sites."host"]`: username/password, a bearer token, headers,
or a browser-exported `cookies.txt`; `~/.netrc` also works. See `docs/interfaces.md`. Voice library format:
`voices/README.md`.
