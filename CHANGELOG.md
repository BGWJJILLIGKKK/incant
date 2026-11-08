# Changelog

All notable changes to incant. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

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
