# Interfaces

Stages talk only through files in `work/<slug>/` (pydantic models in `schemas.py`, I/O via `WorkDir`).
Nothing passes in memory except `Context`.

## Pipeline

| # | Stage | Reads | Writes | Skip-if-unchanged |
|---|---|---|---|---|
| 1 | ingest | source path/URL | `meta.json` (BookMeta), `text.md`, `cover.*` | no |
| 2 | parse | `text.md` | `chapters.json` (ChapterList), `chapters/NNNN.txt`, opt. `characters.json` (CharacterList) | yes |
| 3 | attribute | chapters, characters | `spans/NNNN.jsonl` (Span: kind, text, speaker, emotion) | yes |
| 4 | normalize | spans, lexicons, `say_overrides.json` | `speech/NNNN.json` (ChapterSpeech: spans + `say`, `title_say`) | no |
| 5 | synthesize | speech, `cast.json`, voices | `audio/cache/*`, `audio/chapters/*.wav`, `audio/manifest.json` (AudioManifest), `cast.json` | no (segment cache) |
| 6 | output | manifest, meta, cover | `out/<slug>.<ext>` (or `-o`) | no |

Invariants:
- Chapter ids `NNNN` are stable; span ids are `<chapter>-<n:05d>`.
- Span `text` is never changed after attribute; only `say` is.
- Speaker is a character id, `"narrator"`, or `None` (unattributed).
- `locked: true` on Span or Character is never overwritten by a re-run.
- `pipeline.json`: per-stage `method fingerprint : hash(upstream outputs)`. A change anywhere upstream re-runs downstream.

## Method contract (`stages/base.py`)

```python
class Method:
    Settings: type[BaseModel] = NoSettings     # from [<stage>.<method>] in incant.toml
    SKIP_IF_UNCHANGED: bool = False
    def run(self, ctx: Context) -> object       # required
    @classmethod
    def outputs(cls, wd) -> list[Path]          # per stage, hashed for staleness
    def check(self) -> list[tuple[bool, str]]   # --check readiness
    def fingerprint(self) -> str                # stage + method + settings
```

`Context`: cfg, source, wd, slug, out, out_dir, chapters (restrict per-chapter work), log, results.

### Stage-specific hooks

| Stage | Implement | Base provides |
|---|---|---|
| ingest | `accepts(source) -> bool` (classmethod; drives `auto`), `load(source) -> Ingested(title, paragraphs, source_format, author, cover, warnings…)` | slug, `text.md`, `meta.json`, cover |
| parse | `run`: write chapters (+ characters) | — (`parse/chapters.py` splitter) |
| attribute | `run`: write spans for every chapter in `ctx.chapters` | `spans.py`: deterministic splitting, `keep_locked` |
| normalize | `say(text, kind, title=False) -> str` | lexicon, overrides, `speech/changes.txt` |
| synthesize | `identity() -> str` (cache key), `synthesize(text, voice, emotion, variant) -> WAV bytes`; `NEEDS_VOICE_LIBRARY` | casting, grouping, QA retries, level matching, manifest (`render.py`) |
| output | `EXTENSION`; `run -> Path` (use `default_path(ctx)`) | path resolution |

## Adding things

**A method:**
1. Create `stages/<stage>/<name>/__init__.py` and `<name>.py`, holding exactly one subclass of the stage's `Base`.
2. Optional: a `Settings` pydantic model plus a `[<stage>.<name>]` section in `incant.toml`.
3. The method must write every path in `Base.outputs(wd)`, using `WorkDir` read/write and the schemas, not ad-hoc files.
4. Honour `ctx.chapters` and `locked`.
5. Must not change `Span.text` or ids.
6. Synthesize methods: `identity()` must change whenever output changes, because the segment cache is keyed on it.
7. Quality-affecting methods (parse, attribute, normalize) must be run through `eval/suite.py` with no book regressing by 2 or more quotes.
8. Add a test in `tests/`.
9. Discovery is automatic: there's no registry, and `--list` shows the new method.

**A stage:**
1. Create `stages/<stage>/__init__.py` with `ORDER: int`, `DESCRIPTION: str` and `Base: type[Method]`. `ORDER` sets position; existing stages can be renumbered.
2. Define `outputs()` on `Base`.
3. Add a `[pipeline] <stage> = <default>` entry. Without it, there must be a single method or `accepts()`-based auto-detection.
4. Any new artifact gets a model in `schemas.py` with `schema_version`, plus WorkDir accessors.
5. Downstream stages that read the new artifact must be updated to read it.
6. Method names should be unique across stages so bare CLI tokens resolve. Shared names resolve to the next stage after the previous token; `stage=method` is always explicit.

**A schema field:**
1. Give it a default, so old work dirs still load.
2. If old files can't be read correctly, bump `SCHEMA_VERSION`.
3. If it affects output, it must flow into a hashed output; otherwise skip records won't notice the change.

## Shared services

| Module | Interface | Used by |
|---|---|---|
| `llm.py` | `LLM(cfg, cache_dir).chat_json(system, user, schema, think=, max_tokens=) -> model`; `TokenBudget` caps the shared KV; responses cached by request hash; raises `LLMError` | parse, attribute |
| `voices.py` | `VoiceLibrary(dir).voices / get(id)`; `voices/voices.json`: `{id: {voice, gender, age, tone, default_emotion, …}}` | synthesize, casting, review |
| `casting.py` | `auto_cast(cast, characters, spans, voices, main_unique)`; recomputes only roles in `cast.auto` | synthesize, review |
| `speech.py` | `normalize(text, title=, whole_span=) -> str`: rule-based respelling | normalize/rules |
| `qa.py` | `Transcriber(model, threads).check(wav, text) -> Check(wer, ok())`; `compare`, `words` | synthesize |
| `audio.py` | `to_canonical_wav`, `trim_silence`, `measure_lufs`, `loudnorm`, `wav_duration_ms` (ffmpeg) | synthesize, output |
| `config.py` | `load_config()` ← `incant.toml` + `incant.local.toml`; `[pipeline] [paths] [cast] [llm] [audio] [review] [<stage>.<method>]` | all |

## External services

| Service | Endpoint | Contract |
|---|---|---|
| LLM | `[llm] base_url` (llama-swap, OpenAI-compatible) | `/v1/chat/completions`, `response_format: json_schema`, `chat_template_kwargs.enable_thinking`; 32K shared context |
| TTS | IndexTTS `/v1/audio/speech` | `{model, input, voice, response_format: wav, extra_params: {use_emo_text, emo_text, emo_alpha, use_random}}` → WAV 22.05 kHz |
| ffmpeg | local binary | decode, resample, loudnorm, ebur128, AAC/m4b mux |

Requirements for a new backend:
- **LLM:** OpenAI chat API with JSON-schema-constrained output. If thinking can't be toggled, thinking calls fall back to plain ones.
- **TTS:** a synthesize method returning a WAV per line. It needs named voices, or reference clips with `NEEDS_VOICE_LIBRARY`. Emotion support is optional (ignore `emotion`). `variant > 0` must give a different take for QA retries.

## CLI

```
incant <input|slug|dir> [method…] [out.ext] [-c cfg] [-o out] [--slug] [--from S] [--until S]
          [--force S|all] [--chapters 1,3-5] [--dry-run] [--list] [--check] [--review [--host --port]]
```
- Input is a file or URL (new book), a slug or work dir (resume), or a directory of files (batch, sequential).
- Exit code: non-zero on resolve error or stage failure.
- For a batch, the exit code is non-zero if any book failed.

## Review server (`--review`, `review/server.py`)

| Method | Path | Body / returns |
|---|---|---|
| GET | `/api/book` | meta, chapters, characters, cast, voices |
| GET | `/api/chapter/NNNN` | spans (+ say, segment keys, QA) |
| GET | `/api/lexicon` | `{word: say}` (book) |
| POST | `/api/span` | `{id, speaker?, emotion?, say?}` → locks span; `say` → `say_overrides.json` |
| POST | `/api/cast` | `{character, voice}` sets role (null = back to auto); drops from `cast.auto` |
| POST | `/api/character` | `{id, canonical_name?, gender?, age?}` → locks character |
| POST | `/api/lexicon` | `{word: say}` → `<book>/lexicon.toml` |
| GET | `/audio/segment/<key>.wav`, `/audio/chapter/NNNN.wav`, `/audio/voice/<id>.wav` | audio |

Edits take effect on the next run of the affected stage, which re-runs because its outputs' hash changed. Endpoints are added in `do_GET` / `do_POST` handlers plus a `Book` method. The server is unauthenticated, so bind it to localhost only.

## Eval (`eval/`)

- `eval/gold/<book>.json`: `{book, spans: {span_id: label}}`. A label is a name or alias, `"narrator"`, or `"~a|b"` (substring match against the role).
- `suite.py` re-attributes only the gold chapters and compares against `baseline.json`. It flags `REGRESSION` at a drop of 2 or more quotes, and `--save-baseline` saves the run as the new baseline.
- To add a book:
  1. Ingest and parse it so `work/<slug>/characters.json` exists.
  2. Label every quote in one or more chapters, excluding lines with no single correct answer.
  3. Run `suite.py --books <slug>`, then `--save-baseline`.
- Not yet evaluated: character profiles, emotion, normalize, audio.
