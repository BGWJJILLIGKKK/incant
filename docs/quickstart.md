# Quickstart

From nothing to a finished audiobook with Docker on Linux, Mac or Windows. There are two images:

| Image | Size | Adds |
|---|---|---|
| `incant` | 3.7 GB | everything except the GPU servers: ffmpeg, Chromium, Kokoro voices, speech-to-text QA, Kindle/EPUB/PDF/web ingest |
| `incant_local` | 6.7 GB | + CPU-only PyTorch and Docling: OCR for scanned PDFs |

You need Docker with Compose and git: on Linux, Docker Engine; on a Mac or Windows,
[Docker Desktop](https://docs.docker.com/desktop/) (Windows: with the WSL 2 backend, its default). An LLM
(local server or cloud key) is optional: without one, use the rule-based methods (`basic tags`). IndexTTS is
optional too: Kokoro runs on the CPU.

## 1. Clone and point at a data directory

The data directory holds your books, the working state and the finished audiobooks; the image holds only the
program. Its location, and your user and group IDs, go in a `.env` file in the clone. On a Mac or Windows,
`.env` also adds `compose.desktop.yaml`, which replaces Linux's host networking (see **Docker Desktop**
below).

**Linux**

```sh
mkdir -p ~/audiobooks/books
git clone https://github.com/BGWJJILLIGKKK/incant.git
cd incant
printf 'INCANT_DATA=%s\nUID=%s\nGID=%s\n' ~/audiobooks "$(id -u)" "$(id -g)" > .env
```

Files the container writes to the data directory belong to you (`UID`/`GID`).

**Mac** (Terminal)

```sh
mkdir -p ~/audiobooks/books
git clone https://github.com/BGWJJILLIGKKK/incant.git
cd incant
printf 'INCANT_DATA=%s\nUID=%s\nGID=%s\nCOMPOSE_FILE=compose.yaml:compose.desktop.yaml\n' \
    ~/audiobooks "$(id -u)" "$(id -g)" > .env
```

**Windows** (PowerShell)

```powershell
$data = "$HOME\audiobooks" -replace '\\', '/'
New-Item -ItemType Directory -Force "$data/books" | Out-Null
git clone https://github.com/BGWJJILLIGKKK/incant.git
cd incant
Set-Content .env "INCANT_DATA=$data", "COMPOSE_FILE=compose.yaml;compose.desktop.yaml"
```

The path uses forward slashes (`C:/Users/you/audiobooks`). On Windows the files list separator is `;`, not `:`.
`UID`/`GID` aren't needed: Docker Desktop's file sharing makes files you own either way. In the commands
below, `~/audiobooks` is `$HOME\audiobooks`, and `curl` is `curl.exe` (PowerShell's `curl` is something
else).

A data directory on `C:` is slow for Docker on Windows when there are many small files, and incant's audio
cache is many small files. For faster runs, use a WSL shell (`wsl`, Ubuntu by default) and follow the Mac
steps there, with the data directory in your WSL home.

Optional, in the data directory:
- `.env` with API keys (`CLAUDE_API_KEY`, `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`), `chmod 600`.
- `incant.local.toml` to point at your own servers, e.g. `[llm] base_url = "http://my-gpu-box:8080/v1"`.
  The defaults expect an OpenAI-style LLM and TTS server (such as llama-swap) at `localhost:8080` (README: "Configuration" for Ollama, Claude, Gemini).
  On a Mac or Windows, a server on the same computer is at `host.docker.internal` instead (**Docker Desktop**).

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

This step needs an LLM (step 1); without one, use `basic tags` in place of `characters speakers`.

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
docker compose run --rm incant alice-s-adventures-in-wonderland --review                 # http://localhost:8765 (Mac/Windows: below)
docker compose run --rm incant alice-s-adventures-in-wonderland kokoro mp3 --chapters 4-6  # three more chapters
```

A book already in `work/` is named by its slug; methods you don't repeat come from `[pipeline]` in the config.

Re-runs reuse everything that hasn't changed: the LLM's answers are cached, audio is cached per line, so
fixing one speaker in the review page re-renders only that line.

## Docker Desktop (Mac, Windows)

Linux runs the container on the host's network (`network_mode: host` in `compose.yaml`). Docker Desktop
keeps host networking off by default, so on a Mac or Windows `.env` adds `compose.desktop.yaml` (step 1),
and two things differ:

- **The review page** needs `--service-ports`, so the container's port is published:

  ```sh
  docker compose run --rm --service-ports incant alice-s-adventures-in-wonderland --review   # http://localhost:8765
  ```

  It's published on this computer's `127.0.0.1` only, so other machines can't reach it.
- **Servers on the same computer** (Ollama, LM Studio, llama.cpp, an IndexTTS server) are at
  `host.docker.internal`, not `localhost`. In `<data>/incant.local.toml`:

  ```toml
  [llm]
  dialect = "openai"
  base_url = "http://host.docker.internal:11434/v1"   # Ollama
  model = "qwen3:14b"
  ```

  Servers on other machines, and cloud APIs (Claude, Gemini, ElevenLabs), work unchanged.

The release images are built for both Intel/AMD (`linux/amd64`) and ARM (`linux/arm64`), so Apple Silicon
Macs run them natively, and `docker compose build` builds for your own machine.
If a long run stops with exit code 137, Docker ran out of memory: raise the limit in Docker Desktop's
**Settings → Resources** (`incant_local`'s OCR needs the most).

## Without Docker

Install [uv](https://docs.astral.sh/uv/) and ffmpeg, clone, then `./incant --check`: the launcher installs
its own dependencies on first run. `.venv/bin/playwright install chromium` once for JavaScript-built web
pages. For Docling, add `torch` to the extras the launcher installs:
`INCANT_EXTRAS=qa,browser,kokoro,anthropic,nlp,mobi,torch ./incant …`.
