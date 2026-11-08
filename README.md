# incant

Books in (EPUB, PDF, text, Word/Kindle, web pages), multi-voice audiobooks out (m4b, mp3, or a file per
chapter). An LLM — local or cloud — works out who speaks each line; a TTS engine — local Kokoro, an IndexTTS
or OpenAI-style server, or ElevenLabs — gives each character a voice.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and ffmpeg. `./incant` installs its own dependencies on first run.

```sh
./incant --check                                 # what's installed and reachable
.venv/bin/playwright install chromium               # once: for web pages that load their text with JavaScript
./incant books/book.epub basic tags kokoro       # no LLM, no GPU: rule-based speakers, CPU voices
./incant books/book.epub                         # the configured pipeline (incant.toml [pipeline])
```

The result lands in `output/<author>/<title>/` of your data directory (see **Where things live**).

## Usage

Name one method per stage, in any order; stages you don't name use `[pipeline]` in `incant.toml`, then
auto-detection, then the stage's default.

```sh
./incant book.epub characters speakers indextts m4b   # explicit
./incant book.epub out/test.mp3                       # trailing path: output file (mp3 by extension)
./incant pride-and-prejudice --from synthesize        # resume a book already in work/
./incant pride-and-prejudice --chapters 1-3           # just these chapters
./incant books/ -o audiobooks/                        # batch: every supported file in books/
./incant bookmarks.html --until attribute             # batch: every page in a browser bookmark export
./incant --list                                       # stages and methods (children nested)
```

Also `--until STAGE`, `--force STAGE|all`, `--slug NAME`, `--dry-run`, `-c CONFIG`, `--licenses`.
Re-runs are cheap: parse and attribute are skipped when nothing upstream changed (hand edits survive), and
audio is cached per segment, so only changed lines are re-rendered.

## Choosing methods

| Stage | Methods |
|---|---|
| ingest | `epub` (└ `ebook`: Kindle, Word, ODT, RTF, FB2, HTML via Calibre or Pandoc), `pdf` (└ `docling`: OCR for scans), `txt` (└ `gutenberg`), `url`, `bookmark` |
| parse | `basic` (chapters), `characters` (+ LLM character profiles) |
| attribute | `narrator` (one voice), `tags` (rules), `speakers` (LLM) |
| normalize | `rules` (numbers, Roman numerals, abbreviations… for the TTS), `none` |
| synthesize | `kokoro` (local CPU), `indextts` / `openai` (speech servers), `elevenlabs` (cloud), `dummy` (beeps) |
| output | `m4b`, `mp3`, `chapters` (a file per chapter + playlist) |

Speaker accuracy on the 10-book gold suite (`eval/`):

| You want | parse | attribute | synthesize |
|---|---|---|---|
| Best | `characters` | `speakers` — 95% | `indextts` (GPU server, emotion) or `elevenlabs` |
| Fewer LLM calls | `characters` | `tags` — 80% | `kokoro` |
| No LLM at all | `basic` | `tags` — 68% | `kokoro` (~3–5× real time on a desktop CPU) |

Also done for you: author's notes on web stories ("Thanks for reading!", calls to comment) are removed from
chapter edges, listed in `work/<book>/removed.txt`; TTS respellings are listed in `speech/changes.txt`; your
own pronunciations go in `lexicon.toml` as `[words] Name = "respelling"`. Each TTS backend keeps its own cast,
so switching backends and back keeps your voice choices.

## Where things live

Code (this repository) and data are kept apart. The data directory is named in the git-ignored `.env` beside
`incant.toml` (read by `./incant`, the eval scripts and Docker); without it, the repository is used.

```
.env                        INCANT_DATA=/srv/audiobooks   UID=1000   GID=1000

<data>/
  books/                    your source files
  work/<book>/              per-book state (docs/interfaces.md)
  output/<author>/<title>/  finished audiobooks, each with credits.txt
  incant.local.toml      your settings: logins, model choices, overrides (read last)
  .env                      API keys (chmod 600; --check warns otherwise)
  cookies.txt               browser cookies for sites you log in to
```

Relative paths resolve in the data directory, so `./incant books/book.epub` works from anywhere.

## Configuration

`incant.toml` holds the defaults; put your own changes in `<data>/incant.local.toml`.

**LLM** — any OpenAI-compatible server, or Claude. Keys come from `api_key_env`, looked up in the environment,
then the `.env` files. Each run prints the tokens it used.

```toml
[llm]                                   # local llama.cpp / llama-swap (the default; also vLLM, SGLang)
base_url = "http://localhost:8080/v1"
model = "qwen3.5-9b"

[llm]                                   # Ollama, LM Studio, OpenRouter, OpenAI
dialect = "openai"
base_url = "http://localhost:11434/v1"
model = "qwen3:14b"

[llm]                                   # Claude (official Anthropic SDK)
dialect = "anthropic"
model = "claude-opus-5-5"
api_key_env = "CLAUDE_API_KEY"
context_tokens = 1000000

[llm]                                   # Gemini (Google's OpenAI-compatible endpoint)
dialect = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.8-flash"
api_key_env = "GEMINI_API_KEY"
schema_style = "simple"                 # Gemini rejects parts of JSON Schema
reasoning_effort = "low"                # reasoning tokens count against max_tokens…
min_max_tokens = 16000                  # …so leave room
context_tokens = 1000000
```

On one isolated chapter (847 words, 32 quotes) Claude Opus 5.5 and Gemini 3.8 Flash both scored 81% (≈ $0.10
and < $0.01); the local 9B model 28%. On whole books the local model reaches 95%: other chapters give it
the context.

**ElevenLabs** — `ELEVENLABS_API_KEY` in `<data>/.env`. With `voices_read` and `user_read` permissions, your
account's voices are cast from their labels and the remaining characters are checked before rendering (the
run stops rather than overspend). A free tier (10,000 characters/month, ~15–20 minutes) covers a few
chapters at a time (`--chapters`), and its audio must credit ElevenLabs.

**Logins for web pages** — under `[ingest.url.sites."example.com"]`: username/password, a bearer token,
headers, or `cookies = "cookies.txt"` exported from a logged-in browser; `~/.netrc` also works. Credentials
are only ever sent to their own site.

**Credits** — `credits.txt` beside each audiobook lists what it was made with and under which terms, and what
isn't known. Declare terms you know under `[credits]` (e.g. `"synthesize.indextts" = "non-commercial: …"`);
`./incant --licenses` shows which stages have a method with known permissive terms.

Voice libraries: `voices/README.md`. Every setting: `docs/interfaces.md`.

## Review and edit

```sh
./incant pride-and-prejudice --review      # http://localhost:8765/ (--host 0.0.0.0: no authentication)
```

The script with every quotation coloured by speaker: change a line's speaker, delivery or spoken text and play
its segment; pick voices per character; edit pronunciations; see lines that failed the speech-to-text check.
Edits survive re-runs. The page doesn't render; re-render with `./incant <book> --from normalize --chapters N`.

## Docker

Everything except the GPU servers is in the image (Python, ffmpeg, Chromium, the QA and Kokoro models), so it
also runs offline. Your data directory is mounted at `/data`, and the container runs as your user.

```sh
docker compose build
docker compose run --rm incant --check
docker compose run --rm incant books/book.epub
```

- About 3.3 GB. Build args: `BAKE_MODELS=0` (models download on first use), `EXTRAS=""` (no browser, QA or
  Kokoro), `CONVERTERS=pandoc` or `calibre` (for `ebook`).
- `incant_local` (about 6.7 GB) adds CPU-only PyTorch and Docling, which OCRs scanned PDFs without a text
  layer: `docker compose --profile local build incant_local`. Outside Docker: `uv sync --extra torch`.

## Quality checks

- `eval/suite.py`: speaker attribution on ten books' hand-labelled scenes (~500 quotes) against
  `eval/baseline.json`; flags any book that drops by 2+ quotes. Run it before committing attribution changes.
  `eval/compare.py` scores any parse × attribute combination without touching your data.
- Every rendered segment is transcribed (faster-whisper) and compared with its text; failing takes are
  re-rendered and the best kept. What still fails is listed in `audio/qa_report.json`.

## Development

`uv run pytest`. Stages share data only through files in the work directory; the LLM labels spans but never
writes book text; casting is separate from attribution; audio stays lossless until the output stage.
A method is a directory under `src/incant/stages/<stage>/<name>/`, found by name, and may inherit from
another method of its stage (its settings too). Contracts, file formats and how to add methods or stages:
`docs/interfaces.md`. Plans and results: `docs/roadmap.md`, `docs/findings.md`.
