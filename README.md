# incant — Ingest Novels, Cast Actors, Narrate Tales

Books in (EPUB, PDF, text, Word/Kindle, web pages), multi-voice audiobooks out (m4b, mp3, or a file per
chapter). An LLM — local or cloud — works out who speaks each line; a TTS engine — local Kokoro, an IndexTTS
or OpenAI-style server, or ElevenLabs — gives each character a voice.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and ffmpeg. `./incant` installs its own dependencies on first run.

```sh
git clone https://github.com/BGWJJILLIGKKK/incant.git && cd incant
./incant --check                                       # what's installed and reachable
./incant books/book.epub                               # rule-based speakers, Kokoro voices: no server, no GPU
./incant books/book.epub characters speakers           # better speakers (95%), once an LLM is configured
.venv/bin/playwright install chromium                  # once: for web pages that load their text with JavaScript
```

Out of the box everything runs on the CPU with what's bundled; an LLM (local server or cloud key) and speech
servers are opt-in (**Configuration**). The result lands in `output/<author>/<title>/` of your data directory
(see **Where things live**).
Step by step with Docker on Linux, Mac or Windows, from a fresh clone: [docs/quickstart.md](docs/quickstart.md).

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

In a batch, books overlap where they use different servers: book 2 is fetched and attributed while book 1 is
voiced, but each server only ever works on one book at a time (`[run] lanes`; `--dry-run` shows them).
Also `--until STAGE`, `--force STAGE|all`, `--slug NAME`, `--dry-run`, `-c CONFIG`, `--licenses`.
Re-runs are cheap: parse and attribute are skipped when nothing upstream changed (hand edits survive), and
audio is cached per segment, so only changed lines are re-rendered.

## Choosing methods

| Stage | Methods |
|---|---|
| preingest | `bookmark` (browser bookmark export: every page becomes its own book) |
| ingest | `ebook` (└ `epub`: EPUB 2/3, built in; └ `mobi`: Kindle mobi/azw3; Word, ODT, RTF, FB2, HTML via Calibre or Pandoc), `pdf` (└ `docling`: OCR for scans), `txt` (└ `gutenberg`), `url` (└ `ao3`: Archive of Our Own works, all chapters) |
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
| No LLM at all (the default) | `basic` | `tags` — 68% | `kokoro` (~3–5× real time on a desktop CPU) |

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
  incant.local.toml         your settings: logins, model choices, overrides (read last)
  .env                      API keys (chmod 600; --check warns otherwise)
  cookies.txt               browser cookies for sites you log in to
```

Relative paths resolve in the data directory, so `./incant books/book.epub` works from anywhere.

## Configuration

`incant.toml` holds the defaults; put your own changes in `<data>/incant.local.toml`.

**LLM** — any OpenAI-compatible server, or Claude. Keys come from `api_key_env`, looked up in the environment,
then the `.env` files (`<data>/.env` first; keep them mode 600). Keys never go in a TOML file: the dashboard
shows and edits those, and a config that still has `api_key = "…"` is refused with a message saying what to
change. Each run prints the tokens it used.

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

Several LLMs can sit side by side as named profiles; pick one with `current_llm = "name"`, or per run with
`--llm name`:

```toml
[llm.claude]
dialect = "anthropic"
model = "claude-opus-5-5"
api_key_env = "CLAUDE_API_KEY"

[llm.gemini]
dialect = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
model = "gemini-3.8-flash"
api_key_env = "GEMINI_API_KEY"
schema_style = "simple"
```

On one isolated chapter (847 words, 32 quotes) Claude Opus 5.5 and Gemini 3.8 Flash both scored 81% (≈ $0.10
and < $0.01); the local 9B model 28%. On whole books the local model reaches 95%: other chapters give it
the context.

**ElevenLabs** — `ELEVENLABS_API_KEY` in `<data>/.env`. With `voices_read` and `user_read` permissions, your
account's voices are cast from their labels and the remaining characters are checked before rendering (the
run stops rather than overspend). A free tier (10,000 characters/month, ~15–20 minutes) covers a few
chapters at a time (`--chapters`), and its audio must credit ElevenLabs.

**Logins for web pages** — under `[ingest.url.sites."example.com"]`: `username` with `password_env`, a
`bearer_env` token, `headers_env = { "X-Api-Key" = "VARIABLE" }`, or `cookies = "cookies.txt"` exported from
a logged-in browser; `~/.netrc` also works. The `*_env` settings name variables in the environment or a
`.env` file, as for LLM keys. Credentials are only ever sent to their own site.

**Credits** — `credits.txt` beside each audiobook lists what it was made with and under which terms, and what
isn't known. Declare terms you know under `[credits]` (e.g. `"synthesize.indextts" = "non-commercial: …"`);
`./incant --licenses` shows which stages have a method with known permissive terms.

Voice libraries: `voices/README.md`. Every setting: `docs/interfaces.md`.

## Web dashboard and review

```sh
./incant --serve                          # http://localhost:8765/
docker compose run --rm incant --serve   # Linux with host networking
```

For a system-wide Debian install, build the [native package](packaging/debian/README.md). It provides a
systemd service, system configuration under `/etc/incant`, and persistent service data under `/var/lib/incant`.

The daemon queues full pipeline runs from URLs, uploaded files, data-directory paths, or existing books.
Use **Jobs** to choose methods, preview the plan, and monitor or cancel runs. **Library** links to the
review editor and finished exports. **Configuration** edits the complete TOML for both configuration
layers. Saved settings apply when the next job starts. Changes to the listener address or port require
restarting the daemon.

Job records and logs live in `<data>/.incant/jobs/` and persist across restarts and output-directory cleanup.
Use **Jobs → Clear finished history** to delete finished records and logs. Queued and running jobs,
drafts and exports are kept.

The dashboard binds to `127.0.0.1:8765` by default. For authenticated HTTPS through Caddy on the
same machine, keep that bind and configure `[review].public_domains` and
`[review].trusted_proxy_ips` in TOML. Edit those settings on disk and restart the daemon. Caddy handles
the password prompt and TLS. The [reverse-proxy setup](packaging/debian/README.md#authenticated-https-through-caddy)
also covers running Caddy on another machine.

With Docker Desktop, add `--service-ports` to the Compose run command and use the loopback mapping
in `compose.desktop.yaml`. If the installation's base config is read-only,
the editor saves a complete replacement at `<data>/incant.toml`. The local override is still read last.
Local job paths can use the code directory, the data directory, the configured work directory, or launch-time roots. Add a root with
`./incant --serve --allow-path /mnt/books` or set `INCANT_PATH_ROOTS` to a list separated by the platform's path separator.
`.env` and `.toml` files are never accepted as a book source or an output file, from the dashboard or the
command line, so configuration and keys can't be read into a book or overwritten.

The dashboard has no login of its own: anyone who can reach it can run jobs with your keys. Bind it to
loopback, or put an authenticating proxy in front and set `public_domains` / `trusted_proxy_ips`; it refuses
other addresses without them. Requests must carry a matching `Host` (and, for changes, `Origin`), so other web
pages you visit and rebound DNS names can't drive it.

The single-book review server is also available:

```sh
./incant pride-and-prejudice --review      # http://localhost:8765/
```

It applies the dashboard's address rules: loopback, or an authenticating proxy through `[review]`; from another
computer, forward the port (`ssh -L 8765:127.0.0.1:8765 host`). The script with every quotation coloured by speaker: change a line's speaker, delivery or spoken text and play
its segment; pick voices per character; edit pronunciations; see lines that failed the speech-to-text check.
Edits survive re-runs. Audition span and Re-render chapter use the saved edits and the book’s last recorded
speech backend. To update the exported audiobook, run the CLI command shown in the editor.

## Docker

Everything except the GPU servers is in the image (Python, ffmpeg, Chromium, the QA and Kokoro models), so it
also runs offline. Your data directory is mounted at `/data`, and the container runs as your user.

```sh
docker compose pull                         # release images from ghcr.io/bgwjjilligkkk; or: docker compose build
docker compose run --rm incant --check
docker compose run --rm incant books/book.epub
```

`INCANT_IMAGE` and `INCANT_TAG` in `.env` choose another registry or a fixed version (`INCANT_TAG=0.4.0`).

- About 3.7 GB. Build args: `BAKE_MODELS=0` (models download on first use), `EXTRAS=""` (no browser, QA or
  Kokoro), `CONVERTERS=pandoc` or `calibre` (for `ebook`).
- `incant_local` (about 6.7 GB) adds CPU-only PyTorch and Docling, which OCRs scanned PDFs without a text
  layer: `docker compose --profile local build incant_local`. Outside Docker:
  `INCANT_EXTRAS=qa,browser,kokoro,anthropic,nlp,mobi,torch ./incant …`.
- Mac and Windows (Docker Desktop): add `compose.desktop.yaml` (`COMPOSE_FILE=compose.yaml:compose.desktop.yaml`
  in `.env`; `;` on Windows). It replaces host networking: servers on your computer are at
  `host.docker.internal`, and the review page needs `docker compose run --service-ports`
  ([docs/quickstart.md](docs/quickstart.md)).
- Release images are built by CI from a clean checkout of each `v*` tag (`.github/workflows/images.yml`), for
  `linux/amd64` and `linux/arm64` (Apple Silicon); `tools/build-images.sh [local]` builds them locally for
  your own architecture, from committed files only.

## Quality checks

- `eval/suite.py`: speaker attribution on ten public-domain books' hand-labelled scenes (~500 quotes) against
  `eval/baseline.json`; flags any book that drops by 2+ quotes. The books ship in `eval/books/`, ready to
  attribute. `--attribute tags` needs no LLM; the baseline was made with `speakers` and qwen3.5-9b, so with
  another model save your own baseline first (`--save-baseline`).
  `eval/compare.py` scores any parse × attribute combination without touching your data.
- Every rendered segment is transcribed (faster-whisper) and compared with its text; failing takes are
  re-rendered and the best kept. What still fails is listed in `audio/qa_report.json`.

## Development

`uv run pytest`. Stages share data only through files in the work directory; the LLM labels spans but never
writes book text; casting is separate from attribution; audio stays lossless until the output stage.
A method is a directory under `src/incant/stages/<stage>/<name>/`, found by name, and may inherit from
another method of its stage (its settings too). Contracts, file formats and how to add methods or stages:
`docs/interfaces.md`. Contributing: [CONTRIBUTING.md](CONTRIBUTING.md); changes: [CHANGELOG.md](CHANGELOG.md);
security reports: [SECURITY.md](SECURITY.md).

## License

incant's code is under the BSD 3-Clause license ([LICENSE](LICENSE)). Some of what it depends on and bundles is
copyleft — notably espeak-ng and phonemizer (GPL-3.0, used by Kokoro), KindleUnpack (GPL-3.0, Kindle files)
and ffmpeg (GPL) — so the Docker images are also distributed under those terms. A source install without the
optional extras has no GPL or AGPL code: only permissive licenses, plus num2words (LGPL) and certifi and tld
(MPL). Every component and model, its license and
where its source is: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Responsible use

incant makes audio from text you give it; what you may do with that audio depends on the text, not on incant.

- **Copyright.** Most books and web stories are under copyright. Making an audiobook of one, even for yourself,
  may not be allowed where you live, and sharing it almost never is without the rightsholder's permission.
  Public-domain texts (Project Gutenberg, for example) are the safe case; check your own country's terms.
- **Sites.** Fetching pages with your login or cookies uses your account: follow each site's terms, don't
  bypass paywalls, age gates or access controls, and keep request volumes reasonable. incant identifies itself
  in its user agent and refuses gate pages rather than working around them.
- **Voices.** Use voices you have the right to use. Cloning or imitating a real person's voice needs their
  consent; record it in the voice's `consent` field so `credits.txt` shows it.
- **Services.** LLM and speech providers have their own terms for what you send and what comes back (some
  require attribution or forbid commercial use). `credits.txt` beside each audiobook lists what was used and
  which terms are known, but it is bookkeeping, not legal advice.
- **Disclosure.** If you share what you make, say that it was AI-narrated.

## Ebook mode

The dashboard at `./incant --serve` has **Ebook** and **Audiobook** modes. Both use the same source import and library. Ebook mode shows **Draft** and **Publish** after ingest. Use **Stop after Draft** to review before exporting, or run to completion to produce an EPUB directly. In the ebook library, open a draft to change metadata and to add, reorder, remove, or edit chapters.

```sh
./incant --mode ebook manuscript.md --until draft
./incant --mode ebook manuscript --from publish -o finished.epub
./incant manuscript --until attribute
```

Pass the existing book slug or work directory to reuse the import in either mode. Ebook output defaults to `<output_dir>/ebooks/<slug>.epub`. EPUB sources remain eligible for audiobooks. Ebook publishing refuses EPUB-to-EPUB, including EPUB sources already in the library. Word, Kindle, and other accepted formats can still produce EPUB even when their importer uses an intermediate EPUB.

The ebook editor saves to `work/<slug>/ebook.json`. It preserves chapter order, paragraph text, emphasis, scene breaks, and the imported cover when available. Export uses the same text extraction as Incant’s other mode, so source layout and embedded illustrations can be lost. The ebook draft and export record are separate from the audiobook review and audio output.
