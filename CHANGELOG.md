# Changelog

All notable changes to incant. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## 0.4.0 — 2026-11-25

### Security
- **Keys are no longer read from configuration files.** `api_key` in any LLM profile (selected or not) or
  `[synthesize.*]` table, and the site settings `password`,
  `bearer` and `headers` are refused with a message naming the replacement: put the secret in the
  environment or a `.env` file and name it with `api_key_env`, `password_env`, `bearer_env` or the new
  `headers_env = { "Header" = "VARIABLE" }`. The dashboard shows and edits TOML, so keys there could be read
  by anyone with dashboard access.
- `.env` and `.toml` files are refused as book sources and as output files, on the command line and in the
  dashboard (also through symlinks), and directory batches skip them.
- The single-book review page (`--review`) applies the dashboard's access rules: a matching `Host`, a matching
  `Origin` and a JSON body (≤ 2 MiB) for changes, and loopback unless `[review] public_domains` and
  `trusted_proxy_ips` are set. A web page you visited could previously re-render audio with your TTS keys or
  rewrite the lexicon through it. `--review --host 0.0.0.0` without a proxy is now refused; use
  `ssh -L 8765:127.0.0.1:8765`.
- Dashboard and review responses can't be framed (`X-Frame-Options: DENY`, `frame-ancestors 'none'`), and
  their sockets time out after 30 s without data, including while reading request headers.
- URL ingest refuses private and reserved addresses (loopback, LAN, link-local and cloud metadata, CGNAT,
  multicast) for the URL, every redirect hop and every request a rendered page makes, so a submitted URL can't
  read your own network. `[ingest.url] allow_bogons = true` allows them (pages on your own servers).
- Pandoc conversions read the document under `--sandbox` and drop images, raw HTML and file-naming metadata
  (such as `cover-image`) before writing the EPUB, so a document can't pull local files or URLs into a book.

### Added
- **Pipeline dashboard** (`--serve`): a job queue for full runs from URLs, uploads, data-directory paths or
  existing books, with plans, logs, cancel, a library of finished exports, and an editor for both
  configuration layers. Jobs persist in `<data>/.incant/jobs/`. `--allow-path DIR` / `INCANT_PATH_ROOTS`
  add source directories.
- **Ebook mode** (`--mode ebook`): `draft` and `publish` stages turn an imported source into an editable
  manuscript (`ebook.json`, edited in the dashboard) and a reflowable EPUB.
- **Authenticated access through a reverse proxy:** `[review] public_domains` and `trusted_proxy_ips` let the
  dashboard sit behind Caddy (`basic_auth`) on the same or another machine.
- **Debian package** (`packaging/debian/build`): a systemd service running as an unprivileged `incant` user,
  configuration in `/etc/incant`, data in `/var/lib/incant`, optional extras at build time.
- `headers_env` for per-site request headers taken from the environment.
- Named LLM profiles and per-LLM settings via `current_llm = "name"` (or `[llm] active = "name"`, or CLI `--llm name`), supporting subtables (`[llm.<name>]`), `[llms.<name>]`, and top-level tables (`[<name>]`) with individual context sizes, endpoints, dialects and concurrency.
- In-browser auditioning of single spans and full-chapter re-rendering in the review server (`POST /api/render`) with direct playback.
- Cross-process file-backed lock semaphores (`fcntl.flock`) in `TokenBudget` for coordinating local server token slots across concurrent processes.
- Persistent `gain_baseline.json` so partial runs (`--chapters`) use stable voice gains.
- Whitelisting of character names and lexicon entries during QA word-error-rate calculation to prevent redundant re-synthesis takes.
- Retry logic with backoff in `SpeakersAttribute`, documenting failures in `attribution_errors.json` and marking pipeline records `incomplete` while allowing book generation to finish.
- Hierarchical sentence and clause splitting (major clause -> minor clause -> words) in text preparation to preserve natural speech prosody.
- `ingest=ao3`: Archive of Our Own works. Any URL of a work — the work page, one chapter, a collection
  path — is one book; a multi-chapter work is fetched chapter by chapter (in the site's order) as `# `
  chapters, with title, author and blurb from the work's own header. Adult-rated works are unlocked by
  default (`view_adult = true`); login-restricted works use your cookies, inherited from `url`.
- Batches run in lanes, one per server: while one book is synthesized, the next is fetched and attributed.
  Each server still works on one book at a time; stages on the same server (or, by default, the same machine)
  share a lane. `[run] lanes = "host" | "endpoint" | "off"`; `--dry-run` shows the lanes.

### Changed
- `docker compose pull` fetches the release images from `ghcr.io/bgwjjilligkkk` (`INCANT_IMAGE` / `INCANT_TAG`
  choose another registry or version); `docker compose build` still builds them locally.
- Bounded default Kokoro ONNX CPU threads to half the host core count (capped at 8) to prevent CPU starvation during local synthesis.
- Bookmark files are now a method of a new stage, `preingest` (stage 0, ingest normalization), which
  decides how ingestion is handled: ingest the input as one book, or expand it into a batch. It is
  consulted on the raw input before the pipeline runs, so it is not a per-book step; `bookmark` no longer
  appears among the ingest methods. Naming an ingest method (`ingest=url`) still ingests the file itself.
- Refactored e-book ingest inheritance so `EbookIngest` is the parent class for `EpubIngest` and `MobiIngest`. Both `epub` and `mobi` now inherit directly from `ebook`, simplifying method overrides and cleanly grouping e-book formats under `ebook`.

### Fixed
- Lockfile: pypdfium2 5.15.1 (yanked upstream for a crash at interpreter exit) is now 5.15.2.
- `--check` recognizes Gemini models: its OpenAI-style endpoint lists them as `models/<name>`.
- Backslashes are no longer sent to the TTS. Some web pages contain stray escapes (`doesn\'t`, `\"word\"`)
  that engines read as noise, which failed the speech-to-text check and caused extra re-renders.
- `--from` past ingest on a batch, or on a source file named again, finds each source's existing book
  (it failed with "no book work directory").
- Sites whose redirects pass through plain http (e.g. bouncing through http on their way back to https)
  no longer fail with "refusing to send credentials … over plain http": such hops go to https instead.
  Cookies given to the browser are always marked Secure, so it can't send them over http either.

## 0.3.0 — 2026-11-08

First public release.

### Changed
- Renamed to **incant** (Ingest Novels, Cast Actors, Narrate Tales): the command, the package, `incant.toml`,
  `incant.local.toml`, the `INCANT_*` environment variables and the `incant` / `incant_local` images.
- The default pipeline needs no server: `basic` → `tags` → `rules` → `kokoro` → `m4b`. LLM methods
  (`characters`, `speakers`) and speech servers (`indextts`, `openai`, `elevenlabs`) are opt-in.
- The IndexTTS narrator voice is set under `[synthesize.indextts]` instead of `[cast]`.
- The web user agent identifies incant with the project URL.
- EPUB files are read by incant's own reader (zipfile + lxml) instead of EbookLib, which is AGPL-3.0; the
  text it produces is unchanged.

### Added
- `--version`.
- `--check` with method names and no book checks those methods (`incant --check characters speakers`).
- The ten evaluation books ship in `eval/books/`, so `eval/suite.py` and `eval/compare.py` work from a fresh
  clone; `eval/suite.py --attribute METHOD` (default `speakers`).
- `THIRD_PARTY_NOTICES.md`, the license texts in `LICENSES/`, and `tools/licenses.py`; the images carry them.
- README sections on licensing and responsible use; `CONTRIBUTING.md`, `SECURITY.md`.
- CI: tests and lint on every push and pull request; release images for each `v*` tag, built from a clean
  checkout and pushed to the GitHub Container Registry (and Docker Hub when configured).
  `tools/build-images.sh` builds them locally from committed files only.
- Mac and Windows via Docker Desktop: `compose.desktop.yaml` (normal networking, the review page published on
  127.0.0.1, host servers at `host.docker.internal`), quickstart steps for each, and `INCANT_REVIEW_HOST`.
- Release images for `linux/arm64` as well as `linux/amd64`, each built on a native runner.

### Fixed
- A test that needed the `anthropic` extra now skips without it.

## 0.2.0

Pre-release, not published.

- Ingest: EPUB; Kindle mobi/azw3 without Calibre; Word, ODT, RTF, FB2 and HTML via Calibre or Pandoc; PDF with
  a text layer; scanned PDFs via Docling OCR (`incant_local`); Project Gutenberg text; web pages with logins and
  JavaScript rendering, including multi-part stories; browser bookmark exports as batches.
- Speakers: LLM character profiles and attribution (95% on the 10-book gold suite with a local 9B model), or
  rules without an LLM (68–80%). LLMs: llama.cpp, Ollama, OpenRouter, OpenAI, Claude, Gemini.
- Voices: Kokoro on the CPU (Misaki pronunciation), IndexTTS or any OpenAI-style speech server, ElevenLabs
  with a credit check; automatic casting; each backend keeps its own cast.
- Output: m4b, chaptered mp3 or a file per chapter, in `<author>/<title>/` with `credits.txt`.
- Speech-to-text QA re-renders bad takes; a review page edits speakers, delivery, text and casting.
- Docker images `incant` (3.7 GB) and `incant_local` (6.7 GB, PyTorch and Docling).
