# Quickstart

From nothing to a finished audiobook with Docker, in two variants.

| Image | Size | Adds |
|---|---|---|
| `incant` | 3.7 GB | everything except the GPU servers: ffmpeg, Chromium, Kokoro voices, speech-to-text QA, Kindle/EPUB/PDF/web ingest |
| `incant_local` | 6.7 GB | + CPU-only PyTorch and Docling: OCR for scanned PDFs |

You need Docker with Compose and git. An LLM (local server or cloud key) is optional: without one, use the
rule-based methods (`basic tags`). IndexTTS is optional too: Kokoro runs on the CPU.

## 1. Clone and point at a data directory

```sh
mkdir -p ~/audiobooks/books
git clone https://github.com/BGWJJILLIGKKK/incant.git
cd incant
printf 'INCANT_DATA=%s\nUID=%s\nGID=%s\n' ~/audiobooks "$(id -u)" "$(id -g)" > .env
```

The data directory holds your books, the working state and the finished audiobooks; the image holds only the
program. Files the container writes there belong to you (`UID`/`GID`).

Optional, in the data directory:
- `.env` with API keys (`CLAUDE_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`), `chmod 600`.
- `incant.local.toml` to point at your own servers, e.g. `[llm] base_url = "http://my-gpu-box:8080/v1"`.
  The defaults expect an OpenAI-style LLM and TTS server (such as llama-swap) at `localhost:8080` (README: "Configuration" for Ollama, Claude, Gemini).

## 2. Build and check

```sh
docker compose build                                  # incant
docker compose --profile local build incant_local     # only if you need OCR for scanned PDFs
docker compose run --rm incant --check
```

`--check` lists what the configured pipeline needs and whether it's there. The defaults (rule-based speakers,
Kokoro voices) need nothing else; methods you name that need a server are checked when you name them, e.g.
`--check characters speakers indextts`:

```
ok  ffmpeg
ok  [parse.characters] llm model 'qwen3.5-9b' at http://localhost:8080/v1
ok  [synthesize.indextts] model 'indextts-2.5' at http://localhost:8080/v1
```

## 3. A Kindle book, LLM speakers, Kokoro voices (`incant`)

```sh
curl -L -o ~/audiobooks/books/alice.azw3 https://www.gutenberg.org/ebooks/11.kf8.images
docker compose run --rm incant books/alice.azw3 characters speakers rules kokoro mp3 --chapters 1-3
```

One method per stage: `mobi` reads the Kindle file (picked automatically), `characters` profiles the cast with
the LLM, `speakers` attributes every quotation, `rules` spells out numbers and abbreviations, `kokoro` voices it
on the CPU, `mp3` writes one chaptered file. `--chapters 1-3` renders only the first three chapters (the
character profile always covers the whole book).

## 4. A scanned PDF, rules and IndexTTS (`incant_local`)

A PDF made of page images has no text to extract; `docling` (only in `incant_local`) reads it with layout
analysis and OCR, and is picked automatically for such files. With a scanned book in `books/scan.pdf`:

```sh
docker compose run --rm incant_local books/scan.pdf basic tags none indextts chapters
```

`basic` splits chapters without an LLM, `tags` attributes speakers by rules (dialogue tags, pronouns,
turn-taking), `none` leaves the text as is, `indextts` voices it on the GPU server with emotion, `chapters`
writes a file per chapter plus a playlist.

## 5. Results, review, re-runs

Finished books land in `<data>/output/<author>/<title>/` with a `credits.txt` (what made the book, under which
terms, and what isn't known). Per-book state is in `<data>/work/<book>/`.

```sh
docker compose run --rm incant alice-s-adventures-in-wonderland --review                 # http://127.0.0.1:8765
docker compose run --rm incant alice-s-adventures-in-wonderland kokoro mp3 --chapters 4-6  # three more chapters
```

A book already in `work/` is named by its slug; methods you don't repeat come from `[pipeline]` in the config.

Re-runs reuse everything that hasn't changed: the LLM's answers are cached, audio is cached per line, so
fixing one speaker in the review page re-renders only that line.

## Without Docker

Install [uv](https://docs.astral.sh/uv/) and ffmpeg, clone, then `./incant --check`: the launcher installs
its own dependencies on first run. `.venv/bin/playwright install chromium` once for JavaScript-built web
pages; `uv sync --extra torch` for Docling.
