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
| 1 | `ingest` | `meta.json`, `text.md`, `cover.*` | `epub`, `pdf`, `txt` (└ `gutenberg`), `url`, `bookmark` |
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

The LLM can be a local server or a cloud model; see **LLM servers** below. Each TTS backend has its own voices and its own cast: switching backends keeps the other's `cast.json`
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

## LLM servers

`[llm]` in `incant.toml` (or your data directory's `incant.local.toml`). Keys are named by `api_key_env`
and read from the environment or a `.env` file (the data directory's first); keep that file `chmod 600`
(`--check` warns otherwise). Every run prints the tokens it used.

```toml
# llama.cpp / llama-swap (default; also vLLM, SGLang)
[llm]
base_url = "http://localhost:8080/v1"
model = "qwen3.5-9b"

# Ollama, LM Studio, OpenRouter, OpenAI and other OpenAI-compatible servers
[llm]
dialect = "openai"
base_url = "http://localhost:11434/v1"   # OpenRouter: https://openrouter.ai/api/v1
model = "qwen3:14b"
# api_key_env = "OPENROUTER_API_KEY"
# structured = "json_object"             # if the server can't constrain answers to a JSON schema

# Claude, through the official Anthropic SDK (extra: anthropic)
[llm]
dialect = "anthropic"
model = "claude-opus-5-5"
api_key_env = "ANTHROPIC_API_KEY"
context_tokens = 1000000
concurrency = 4

# Gemini, through Google's OpenAI-compatible endpoint
[llm]
dialect = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.8-flash"               # GET …/openai/models lists what your key can use
api_key_env = "GEMINI_API_KEY"
context_tokens = 1000000
```

Long-context models can profile a whole book's characters in one pass: `[parse.characters] window_words = 200000`.
Cloud calls retry overloads and rate limits (`[llm] retries`, backoff up to 32 s).

Measured on Pride and Prejudice chapter 1 alone (847 words, 32 gold quotes; `characters` + `speakers`):

| Model | Speakers right | Time | Tokens (in / out) |
|---|---|---|---|
| qwen3.5-9b (local) | 9/32 (28%) | 27 s | 4.6k / 3.1k |
| claude-opus-5-5 | 26/32 (81%) | 26 s | 9.1k / 3.1k (≈ $0.10) |

A single short chapter is the hard case: Mrs. Bennet is "his lady" or "his wife" until the next chapter, so
the local model gives her lines to Mrs. Long. On whole books the local model reaches 95% (`eval/`).

## Credits and license terms

Each finished audiobook gets a `credits.txt` beside it: the source, every method, model and voice used, the
license and the terms each puts on the result, required attributions, the weakest known terms, and the gaps
(whatever incant wasn't told). Nothing is required: unknown terms are listed, never assumed. Declare what you
know in config:

```toml
[credits]
llm = "provider terms: Qwen license"
"synthesize.indextts" = "non-commercial: check your checkpoint's license"
"voice.a_janelle_risa" = "attribution: voice-zero"
source = "none: public domain"
```

`./incant --licenses` lists every method's known terms and whether a chain with known permissive terms exists
for every stage (today: `parse=basic attribute=narrator|tags synthesize=kokoro`), naming the stages where none does.

## Where things live

The code (this repository) and your data are kept apart. The **data directory** holds everything that is yours:

```
<data>/
  books/                  source files (epub, pdf, txt…)
  work/<book>/            per-book working state (see docs/interfaces.md)
  output/<author>/<title>/  finished audiobooks
  incant.local.toml    your settings and secrets (read last; never committed)
  cookies.txt             browser cookies for sites you log in to (cookies = "cookies.txt")
```

It is named once, in the git-ignored `.env` next to `incant.toml`, which `./incant`, the config loader,
the eval scripts and `docker compose` all read:

```sh
# .env
INCANT_DATA=/srv/audiobooks
UID=1000
GID=1000
```

`$INCANT_DATA` in the environment overrides it; without either, the repository itself is the data directory.
Relative input paths that don't exist where you are resolve in the data directory, so
`./incant books/book.epub` works from anywhere; relative `cookies` paths do too.

## Run with Docker

Everything but the GPU servers (LLM, IndexTTS) is in the image: Python and its packages, ffmpeg, headless
Chromium, the speech-to-text QA model and Kokoro's voices (baked in, so it also runs offline). The data
directory from `.env` is mounted at `/data`; nothing of yours is in the image.

```sh
docker compose build                                  # or: docker build -t incant .
docker compose run --rm incant --check             # reaches localhost:8080 through host networking
docker compose run --rm incant books/book.epub     # paths relative to the data directory
docker compose run --rm incant book-slug --review  # host networking: http://127.0.0.1:8765
```

- Runs as `UID:GID` from `.env`, so files in the data directory stay yours.
- Lighter images: `--build-arg BAKE_MODELS=0` (models download on first use) or `--build-arg EXTRAS=""`
  (no browser, QA or Kokoro: ~1.1 GB instead of ~3.3 GB).
- The code changes? `docker compose build` again; your data is untouched.

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
