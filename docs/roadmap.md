# Roadmap: stage alternatives

**Status: complete** (6 phases; results at the end). Which alternatives from the stage-alternatives survey get
added, how, and in what order. The filter: realistic
without a tremendous amount of work, fills a gap, and doesn't bloat the project. PyTorch-based options go into a
separate image, **`incant_local`**, built from the same Dockerfile; the default `incant` image stays lean.

## Decisions

| Item | Lands in | Gate before merging | Outcome |
|---|---|---|---|
| Secrets from `.env` + cloud LLM presets (Gemini, Claude, OpenRouter, Ollama) | core | live smoke test + one gold book | **shipped** — Claude and Gemini 81% on the test chapter |
| `ingest=ebook` — Calibre / Pandoc, child of `epub` | core (tools via Docker `CONVERTERS`) | real docx/odt/mobi conversions | **shipped** — docx, odt verified |
| Credits / license report + `--licenses` gap report | core | — | **shipped** |
| `synthesize=elevenlabs` | core | mock tests (live once a key exists) | **shipped** — verified live |
| Misaki pronunciation for Kokoro | `nlp` extra, lean image | A/B: whisper WER not worse + listening | **shipped as the default** (with a bow/bowed rule) |
| `parse=names` — spaCy name roster, no LLM | `nlp` extra, lean image | no-LLM suite beats 68% by ≥ 5 points | dropped — 67.9% |
| `parse=booknlp` | `torch` extra → `incant_local` | no-LLM suite ≥ 73% (the bar `names` missed) | dropped — 62.5% |
| `ingest=docling` — child of `pdf` | `torch` extra → `incant_local` | scanned-PDF text clearly better than `pdf` | **shipped** — `incant_local`, 6.7 GB |

**Not added:** FastCoref (BookNLP covers coreference), CTC alignment QA (whisper QA exists), NeMo normalization
(small gain over `rules`; pynini + transformers), StyleTTS 2 / F5-TTS (GPU; usable through the `openai` method
when served), Piper (overlaps Kokoro; now GPL-3), Crawl4AI (Playwright + trafilatura), spaCy dialogue rules and
the dialogue alternator (both in `attribute=tags`), ChatTTS / XTTS-v2 (non-commercial, heavy).

## Phases

*The plan as written before implementation; outcomes are under Results.*

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
  Gemini (Google's OpenAI-compatible endpoint) needed `schema_style = "simple"` (it rejects length limits in
  JSON schemas), `reasoning_effort` / `min_max_tokens` (3.x models reason within max_tokens) and low concurrency
  on a free key; gemini-3.8-flash then scored 26/32 (81%, same as Claude) in 19 s for under a cent;
  gemini-3.5-flash-lite 11/32 (34%).
- **Phase 2** — `ingest=ebook`: docx and odt converted by Pandoc in a `CONVERTERS=pandoc` image, read by `epub`.
- **Phase 3** — `credits.txt` beside every audiobook and `--licenses`; a chain with known permissive terms
  exists (`parse=basic attribute=narrator|tags synthesize=kokoro`).
- **Phase 4** — `synthesize=elevenlabs` on the shared HTTP client (new `request` / `decode` / `auth_headers`
  hooks; IndexTTS and `openai` requests unchanged) with a quota guard. Request shape checked against the API
  reference and tested against a mock server. Live (free tier, scoped key): synthesis works; the key may not list
  voices (`voices_read`) or read the subscription (`user_read`) — both handled: six stock voices that synthesize on
  a free tier are built in (others answer 402 paid_plan_required or 404), and when the credits can't be read the
  run stops unless `allow_over_quota = true`. A 2-chapter test book (187 characters) rendered with QA clean.
  With `voices_read` and `user_read` granted: 21 account voices (all synthesize on the free tier; 20 castable,
  one gender-neutral), labels mapped to casting tags (ElevenLabs' "young" → neutral, tone also from the voice
  name: "Deep", "Bright"); the credit check reads the balance; a test book cast Mr. Hale → Roger, Margaret →
  Alice, narrator George, QA clean.
- **Phase 5** — `eval/compare.py` (any parse × attribute on scratch copies; strict + loose scores) and
  `eval/g2p_ab.py`. **Misaki** (extra `nlp`, no PyTorch, +110 MB): on par with espeak by speech-to-text (mean WER
  0.036 vs 0.037, 3/70 failing each). Listening to the 10 heteronym/name sentences: misaki better on read/read and
  wound/wound, worse on "bowed to" (it always says the violin "bow"); after a context rule for bow/bowed/bowing
  (`kokoro/heteronyms.py`) misaki matched or beat espeak on all ten → `g2p = "auto"` (misaki when installed) is the
  default, and `nlp` joins the default extras and image. Both still miss "lead pipe" and "tear" (paper). **`parse=names`** (spaCy
  person names) **missed its gate and was removed**: as a full roster 62.7% (non-speakers crowd pronoun
  resolution), as enrichment of the speaking roster 67.9% — equal to `basic` + `tags` (needed ≥ 73%). Names alone
  can't give what the LLM roster gives `tags` (79.5%): role descriptions ("Scrooge's nephew", "wife of
  Mr. Bennet"). The tags roster code is now shared (`roster_from_names`, `tag_name_counts`).
- **Phase 6** — `torch` extra (PyTorch and torchvision from the CPU-only index) and the `incant_local` image (6.7 GB);
  the model bake step now fails the build on any error. **Docling passed its gate**: an image-only scan gives
  `pdf` nothing (it points to ocrmypdf) and Docling clean text (~7 s/page); running headers are dropped.
  **BookNLP missed its gate and was removed**: it installs on Python 3.12 only with workarounds (saved models
  carry `position_ids` that current transformers reject; TensorFlow declared but unused, 1.9 GB; `pkg_resources`),
  and its small models scored 62.0% as the character list for `tags`, 62.5% with its own quote speakers as hints
  — below `basic` + `tags` (67.9%): its coreference splits people ("his wife" / "Mrs. Bennet": P&P 23%).
  The large models (+2.7 GB in the image) weren't tried: the failure is the clustering, not model size.
