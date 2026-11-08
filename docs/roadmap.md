# Roadmap: stage alternatives

Which alternatives from the stage-alternatives survey get added, how, and in what order. The filter: realistic
without a tremendous amount of work, fills a gap, and doesn't bloat the project. PyTorch-based options go into a
separate image, **`incant_local`**, built from the same Dockerfile; the default `incant` image stays lean.

## Decisions

| Item | Lands in | Gate before merging |
|---|---|---|
| Secrets from `.env` + cloud LLM presets (Gemini, Claude, OpenRouter, Ollama) | core | live smoke test + one gold book |
| `ingest=ebook` — Calibre / Pandoc, child of `epub` | core (tools via Docker `CONVERTERS`) | real docx/odt/mobi conversions |
| Credits / license report + `--licenses` gap report | core | — |
| `synthesize=elevenlabs` | core | mock tests (live once a key exists) |
| Misaki pronunciation for Kokoro | `nlp` extra, lean image | A/B: whisper WER not worse + listening |
| `parse=names` — spaCy name roster, no LLM | `nlp` extra, lean image | no-LLM suite beats 68% by ≥ 5 points |
| `parse=booknlp` | `torch` extra → `incant_local` | no-LLM suite ≥ 73% (the bar `names` missed) |
| `ingest=docling` — child of `pdf` | `torch` extra → `incant_local` | scanned-PDF text clearly better than `pdf` |

**Not added:** FastCoref (BookNLP covers coreference), CTC alignment QA (whisper QA exists), NeMo normalization
(small gain over `rules`; pynini + transformers), StyleTTS 2 / F5-TTS (GPU; usable through the `openai` method
when served), Piper (overlaps Kokoro; now GPL-3), Crawl4AI (Playwright + trafilatura), spaCy dialogue rules and
the dialogue alternator (both in `attribute=tags`), ChatTTS / XTTS-v2 (non-commercial, heavy).

## Phases

Each phase: tests for the new code, the full suite green, defaults unchanged, quality measured on the 10-book
gold suite, nothing written to the data directory by tests, then commit.

### 1. Secrets and cloud LLMs
- `secret(name)`: process environment, then the data directory's `.env`, then the project's `.env`. Used for
  `[llm] api_key_env`, speech `api_key_env`, URL `password_env` / `bearer_env`. Never logged or fingerprinted.
- `--check` warns about a group/world-readable `.env` that holds keys.
- Tested presets for Gemini and Claude (OpenAI-compatible endpoints), OpenRouter, Ollama, llama.cpp; single-pass
  cast extraction for long-context models (`[parse.characters] window_words`, `[llm] context_tokens`).
- Done when: `--check` passes per provider and a short book plus one gold book run end to end, with accuracy,
  time and token cost recorded here.

### 2. `ingest=ebook`
- `EbookIngest(EpubIngest)`: mobi / azw / azw3 / docx / odt / rtf / fb2 / lit / pdb / local HTML (not bookmark
  exports) → EPUB with `ebook-convert` or `pandoc` → the `epub` reader. `converter = auto | calibre | pandoc`.
- Dockerfile `ARG CONVERTERS=""` (`pandoc`, `calibre`).
- Done when: a docx and an odt convert in the image built with `CONVERTERS=pandoc`.

### 3. Credits and license report
- Methods may declare `LICENSE` / `OUTPUT_TERMS` (unknown when absent); a `[credits]` config table holds terms
  the user knows (LLM, IndexTTS…); voices use their `license` / `source` / `consent` fields. Never required.
- The output stage writes `credits.txt` beside the audiobook: source, methods, models and servers, voices,
  required attributions, the weakest known terms, and the gaps (what is unknown).
- `incant --licenses`: known terms per stage, and the stages where no method with known permissive terms exists.

### 4. `synthesize=elevenlabs`
- On the shared HTTP speech client (new `request()` / `decode()` hooks; IndexTTS and `openai` requests unchanged).
- Account voices with their gender/age labels for casting, `voices/elevenlabs.json` overrides, a seed per QA retry,
  and a quota guard: stop before rendering more characters than the subscription has left.

### 5. `nlp` extra: Misaki and `parse=names` (lean image)
- First, `eval/compare.py`: score any parse × attribute combination on scratch copies of the gold books.
- Extra `nlp`: spaCy + `en_core_web_sm`, Misaki without its `[en]` extra (which pulls in PyTorch).
- Kokoro `g2p = espeak | misaki`; default changes only if the A/B wins.
- `NamesParse(BasicParse)`: people's names, aliases, gender from titles and pronouns, mention counts.
- Either piece shipping adds `nlp` to the default image (~150 MB).

### 6. `incant_local` (PyTorch)
- Extra `torch` (BookNLP, Docling) with PyTorch from the CPU-only index, so the lean install never touches it.
- `docker build --build-arg EXTRAS="qa browser kokoro nlp torch" -t incant_local .` (~6–7 GB, models baked).
- `BooknlpParse`: characters from BookNLP's entities and coreference. `DoclingIngest(PdfIngest)`: layout and OCR
  for PDFs whose text layer is missing or poor.
- `--check` and `--list` say which methods need `incant_local`.

## Risks
- BookNLP is old and requires TensorFlow: confirm it installs on Python 3.12 before writing code for it.
- Anthropic's OpenAI-compatible endpoint may ignore structured output: fall back to `structured = "json_object"`.
- ElevenLabs is untested live until a key exists.
- Misaki pins Python < 3.13.

## Results
- **Phase 1** — secrets from `.env` (environment → data directory → project), `--check` warns about readable
  `.env` files; Claude through the official Anthropic SDK (`dialect = "anthropic"`, structured output, refusal
  fallback); retries with backoff for overloads. P&P chapter 1 alone (847 words, 32 gold quotes):
  claude-opus-5-5 26/32 (81%) in 26 s, 9.1k/3.1k tokens (≈ $0.10); local qwen3.5-9b 9/32 (28%) — on one
  isolated chapter the local model lacks the context other chapters give it (95% on whole books).
  Gemini: every model returned 503 "high demand" during the tests; to retry.
- **Phase 2** — `ingest=ebook`: docx and odt converted by Pandoc in a `CONVERTERS=pandoc` image, read by `epub`.
- **Phase 3** — `credits.txt` beside every audiobook and `--licenses`; a chain with known permissive terms
  exists (`parse=basic attribute=narrator|tags synthesize=kokoro`).
- **Phase 4** — `synthesize=elevenlabs` on the shared HTTP client (new `request` / `decode` / `auth_headers`
  hooks; IndexTTS and `openai` requests unchanged) with a quota guard. Request shape checked against the API
  reference and tested against a mock server; live test pending a key.
- **Phase 5** — `eval/compare.py` (any parse × attribute on scratch copies; strict + loose scores) and
  `eval/g2p_ab.py`. **Misaki** (extra `nlp`, no PyTorch, +110 MB): on par with espeak by speech-to-text (mean WER
  0.036 vs 0.037, 3/70 failing each; fixes "wound/wound", slightly worse on a few names) → shipped as
  `[synthesize.kokoro] g2p = "misaki"`, default unchanged, not in the default image. **`parse=names`** (spaCy
  person names) **missed its gate and was removed**: as a full roster 62.7% (non-speakers crowd pronoun
  resolution), as enrichment of the speaking roster 67.9% — equal to `basic` + `tags` (needed ≥ 73%). Names alone
  can't give what the LLM roster gives `tags` (79.5%): role descriptions ("Scrooge's nephew", "wife of
  Mr. Bennet"). The tags roster code is now shared (`roster_from_names`, `tag_name_counts`).
