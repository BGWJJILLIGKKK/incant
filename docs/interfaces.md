# Interfaces

Stages share data only through files in the book's work directory. No data passes between stages in memory except
`Context`. Every structured file is a pydantic model in `src/incant/schemas.py` and is read and written through
`WorkDir` (`src/incant/workdir.py`).

General rules for every file below:
- **Location:** `<work_dir>/<slug>/`, where `work_dir` is `[paths] work_dir` (default `work/`, relative to
  the project home) and `slug` is the ingested title, slugified (or `--slug`).
- **Writes are atomic:** temp file plus rename, via `workdir.atomic_write`. Half-written files never exist.
- **JSON:** UTF-8, 2-space indent, fields that are `null` are **omitted** (`exclude_none`). Readers must treat
  a missing optional field as its default.
- **Hand-editable:** any file may be edited by hand as long as it still validates against its model.
- **`schema_version`:** currently 1, on every top-level model. Bump it for changes old readers can't handle.
- **Ids:**
  - chapter id `NNNN`: 4-digit, 1-based, in reading order;
  - span id `NNNN-nnnnn`: chapter id + 5-digit, 1-based span number within the chapter;
  - character id: slug of the canonical name (`elizabeth-bennet`), `minor-NNNN-<role-slug>` for unnamed
    speakers, or the reserved `narrator`;
  - voice id: a key of `voices/voices.json`.

## Directory layout

```
work/<slug>/
  meta.json               ingest       BookMeta
  text.md                 ingest       whole book, normalized text
  cover.<ext>             ingest       optional image
  chapters.json           parse        ChapterList
  chapters/NNNN.txt       parse        one chapter's text
  characters.json         parse (+attribute, review)   CharacterList
  spans/NNNN.jsonl        attribute (+review)          one Span per line
  speech/NNNN.json        normalize    ChapterSpeech
  speech/changes.txt      normalize    human report
  lexicon.toml            human/review per-book pronunciations
  say_overrides.json      review       hand-edited spoken text
  cast.json               synthesize (+review, human)  Cast
  audio/cache/XX/<key>.wav        synthesize   one rendered segment
  audio/cache/XX/<key>.qa.json    synthesize   QA record for that segment
  audio/cache/XX/<key>.lvl.json   synthesize   loudness of that segment
  audio/chapters/NNNN.wav         synthesize   assembled chapter
  audio/chapters/NNNN.json        synthesize   assembly fingerprint
  audio/manifest.json             synthesize   AudioManifest
  audio/qa_report.json            synthesize   segments still failing QA
  audio/concat.txt, ffmetadata.txt  output     ffmpeg inputs (scratch)
  out/<slug>.m4b                  output       final file (or -o path)
  llm_cache/XX/<sha256>.json      parse, attribute   cached LLM answers
  pipeline.json                   runner       stage skip records
```
`XX` = first two hex characters of the key.

---

## Stage 1 — ingest

**Input:** a file path or URL from the CLI.
**In-memory contract:** `load(source) -> Ingested`, defined in `stages/ingest/base.py`. The base class writes the files.

| `Ingested` field | Type | Req | Meaning |
|---|---|---|---|
| title | str | yes | book title; also the default slug |
| paragraphs | list[str] | yes | body in reading order, already normalized (see `text.md`) |
| source_format | `txt`\|`epub`\|`url`\|`md`\|`pdf` | yes | |
| author | str | no | |
| language | str | no, `en` | BCP-47 |
| description | str | no | blurb, becomes the m4b comment |
| cover | (ext, bytes) | no | e.g. `("jpg", b"...")` |
| warnings | list[str] | no | logged |

### `meta.json` — BookMeta
| Field | Type | Req | Meaning |
|---|---|---|---|
| schema_version | int | default 1 | |
| slug | str | yes | work-dir name; `[a-z0-9-]`, ≤ 60 characters |
| title | str | yes | m4b title/album |
| author | str | no | m4b artist |
| language | str | default `en` | |
| source | str | yes | absolute path or URL of the input |
| source_format | enum as above | yes | |
| cover | str | no | file name in the work dir (`cover.jpg`); must exist if set |
| series, series_index | str, float | no | reserved; not yet used |
| description | str | no | m4b comment (first 2000 characters) |

### `text.md`
UTF-8 plain text. The paragraph and heading conventions are produced by `normalize.render_text_md`.
- One paragraph per line, paragraphs separated by exactly one blank line, with a trailing newline.
- `# Title` is a structural heading (chapter boundary). A body paragraph that happens to start with `# ` is escaped as `\# `.
- `* * *` is a scene break: never first, never repeated, never directly after a heading.
- `*word*` marks emphasis. It is kept through to TTS input, where it is stripped.
- Quotes stay as in the source (curly or straight). **Dialogue detection depends on them.** Dashes are kept too.
- Whitespace is collapsed, text is Unicode NFC, ligatures are expanded, Gutenberg boilerplate and
  `[Illustration]` blocks are removed.
- Should not contain: page numbers, running headers, footnote markers, a table of contents, or hyphenation from line breaks.

### `cover.<ext>`
An image (`jpg`/`png`) attached as m4b cover art. Optional.

**Requirements for a new ingester:**
- `accepts(source)` must be cheap: check the extension or URL scheme, not the content.
- Emit chapter boundaries as `# ` headings if the source has structure (EPUB toc, PDF headings).
  Without them, parse falls back to pattern detection.
- Never emit text that shouldn't be read aloud.

---

## Stage 2 — parse

**Reads:** `meta.json` (title, used for single-chapter books) and `text.md`.

Chapters are split deterministically, by `stages/parse/chapters.py`. Every parse method must use it or give the same output:
- Headings are taken from `# ` lines if any exist, otherwise from patterns ("Chapter XII", "PROLOGUE", "IV."),
  otherwise the whole book becomes one chapter.
- Text before the first heading is kept as "Front Matter" if it has at least 40 words.
- A run of 3 or more empty headings is treated as a contents list and dropped. Empty "Part/Book/Volume" headings are folded into the next title.
- Chapters titled Contents or List of Illustrations are dropped.
- Leading chapters under 40 words with no sentence (title-page fragments) are dropped.
- Chapter ids follow the order of `text.md`. If `text.md` changes, the ids may change, and every downstream file is then invalid.

### `chapters.json` — ChapterList
| Field | Type | Req | Meaning |
|---|---|---|---|
| schema_version | int | 1 | |
| method | `headings`\|`pattern`\|`llm`\|`single` | yes | how chapters were found |
| chapters[] | Chapter | yes | in reading order |
| .id | str | yes | `NNNN` |
| .index | int | yes | 1-based, equal to `int(id)` |
| .title | str | yes | as printed ("CHAPTER III."); spoken unless `speak_title` is false |
| .file | str | yes | `chapters/NNNN.txt`, relative to the work dir |
| .word_count | int | yes | scene breaks excluded |
| .speak_title | bool | default true | announce the title at the start of the chapter audio |

### `chapters/NNNN.txt`
The chapter body in the `text.md` format, without the heading, without leading or trailing scene breaks, and with the `\# ` escape removed. **This file is the text of record:** spans must rebuild it exactly.

### `characters.json` — CharacterList
Written by `parse/characters`; `parse/basic` deletes it. Attribute and the review page update it (see below).

| Field | Type | Req | Meaning |
|---|---|---|---|
| schema_version | int | 1 | |
| characters[] | Character | yes | sorted by mentions, descending |
| ambiguous_aliases | {alias: [char id]} | default {} | names several characters share ("Miss Bennet"); attribution must decide from context |
| narrator | str\|null | default null | null = third person; a character id = first-person narrator; `narrator` = unnamed first person. **Set by attribute.** |

Character fields:

| Field | Type | Default | Meaning / used by |
|---|---|---|---|
| id | str | required | see Ids |
| canonical_name | str | required | most-used proper name, upgraded to a fuller form that contains it if that form has ≥ 10% of the uses |
| aliases | list[str] | [] | every other name or title the text uses; attribution matching, eval |
| gender | `male`\|`female`\|`nonbinary`\|`unknown` | unknown | casting |
| gender_confidence | float 0–1 | 0 | |
| gender_evidence | str | – | pronoun or quote |
| age | `child`\|`teen`\|`young_adult`\|`adult`\|`elderly`\|`unknown` | unknown | casting |
| description | str | – | one line; attribution roster, role matching |
| accent_hint | str | – | reserved |
| first_chapter | str | – | chapter id |
| chapters | list[str] | [] | chapters the character appears in; drives the attribution roster |
| mentions | int | 0 | text windows mentioning the character (importance) |
| speaks | bool | false | has quoted lines; only speakers are cast |
| line_count | int | 0 | lines attributed; **set by attribute (full runs only)**; casting priority |
| locked | bool | false | set by a human; re-runs keep the entry unchanged |
| origin | `profile`\|`attribution` | profile | `attribution` = unnamed speaker created by attribute, rebuilt every run unless locked |

---

## Stage 3 — attribute

**Reads:** `chapters.json`, `chapters/*.txt`, `characters.json` (required by `speakers`, ignored by `narrator`).
**Writes:**
- `spans/NNNN.jsonl` for every chapter. Only the `ctx.chapters` chapters are relabelled; any missing ones are created unlabelled.
- `characters.json`: sets `narrator`, rebuilds the `origin=attribution` characters and, on full runs, sets `line_count`.

Spans are split deterministically, in code (`stages/attribute/spans.py`):
- Paragraphs are split on `“ ”` and straight `"`. A quote may run unclosed to the end of its paragraph.
- Narration with nothing speakable (", " or " — ") is merged into the neighbouring span.
- Scene breaks become their own narration span (`* * *`).
- **Invariant:** joining span texts within a paragraph with `""`, and paragraphs with `"\n\n"`, reproduces
  `chapters/NNNN.txt` minus its trailing newline. This is asserted.
- Not handled: ‘single-quote’ dialogue, em-dash dialogue, «guillemets».

### `spans/NNNN.jsonl` — one `Span` JSON per line, in text order
| Field | Type | Req | Meaning |
|---|---|---|---|
| id | str | yes | `NNNN-nnnnn` |
| chapter_id | str | yes | |
| para | int | yes | 0-based paragraph index in the chapter file |
| kind | `narration`\|`dialogue` | yes | |
| text | str | yes | exact source text, quote marks and emphasis included. **Never altered after splitting.** |
| speaker | str\|null | – | character id; `narration` spans use `narrator`; null = unknown (rendered in the default voice) |
| emotion | {text: str ≤ 40 characters, alpha: float 0–1 (default 0.6)} | – | delivery cue for the TTS; absent = neutral |
| confidence | float | – | reserved |
| locked | bool | false | hand-reviewed. A re-split keeps speaker and emotion where the id **and** text still match. |
| say | str | – | never set here (see normalize) |

Speaker mapping rules for any attribute method:
- In first-person books, the narrator's narration and lines go to the `characters.narrator` id, so they share one voice.
- Unnamed speakers become `minor-NNNN-<role>` characters (`origin: attribution`), one per chapter and role.
  A role that matches an existing alias maps to that character instead.
- Every speaker id must exist in `characters.json`, or be `narrator`.

---

## Stage 4 — normalize

**Reads:**
- `chapters.json`, `spans/*.jsonl`
- `lexicon.toml` (project) then `<book>/lexicon.toml` (book wins)
- `say_overrides.json`

**Writes:** `speech/NNNN.json` for every chapter, and `speech/changes.txt`.

**Method contract:** `say(text, kind, title=False) -> str`. It turns source text into words the TTS reads correctly.
- Strip quote marks and emphasis.
- Spell out numbers, years, money, times, Roman numerals, abbreviations, symbols and all-caps words.

The base class then applies, in order:
1. the method's `say()`;
2. the lexicon (whole words, case-insensitive);
3. `say_overrides.json`, if the stored `text` still equals the span text.

### `speech/NNNN.json` — ChapterSpeech
| Field | Type | Meaning |
|---|---|---|
| schema_version | int | |
| chapter_id | str | |
| title_say | str | spoken chapter title ("Chapter three.") |
| spans[] | Span | the attribute spans, unchanged except for `say` (trimmed TTS text, no markup) |

### `speech/changes.txt`
Human-readable list of every span whose spoken text differs in more than punctuation: span id, then `- before` / `+ after` windows.

### `lexicon.toml` (project root, then per book)
```toml
[words]
Hermione = "Her-my-oh-nee"
```
Keys are matched as whole words, case-insensitive; the longest key wins.

### `say_overrides.json`
`{span_id: {"text": <span text when edited>, "say": <spoken text>}}`. An entry is ignored once the span's text changes.

---

## Stage 5 — synthesize

**Reads:**
- `chapters.json`, and `speech/NNNN.json` (falls back to `spans/` when there is none)
- `characters.json`, `cast.json`
- `voices/voices.json`
- config `[audio]`, `[cast]`

**Writes:**
- `cast.json`
- `audio/cache/**`, `audio/chapters/NNNN.{wav,json}`
- `audio/manifest.json`, `audio/qa_report.json`

### `cast.json` — Cast
| Field | Type | Meaning |
|---|---|---|
| schema_version | int | |
| roles | {char id: voice id} | must include `narrator` (from `[cast] narrator`) |
| default_voice | str | voice for unknown or uncast speakers; defaults to the narrator voice |
| auto | {char id: voice id} | the last automatic pick. A role equal to its `auto` entry is recast every run; a role that differs (hand-set) is kept. |

Casting (`casting.auto_cast`):
- Every character with at least one attributed line and no role gets a voice.
- Candidates are voices tagged `male`/`female` that match the character's gender. A character of unknown gender may take any of them. The narrator's voice is avoided where possible.
- Ranked by: fewest chapters shared with the voice's other characters, not already used by a main character, unused, best age match, best tone match, fewest lines.
- No candidate means the character falls back to `default_voice`.
- The `[cast] main_unique` characters with the most lines never share a voice, and scene partners avoid sharing one.
- First-person books (with `[cast] first_person_narration = "character"`): the `narrator` role takes the narrating character's voice.

### Voice library: `voices/voices.json` (+ optional `voices/<id>.json` overrides)
`{voice_id: {voice, source, default_emotion, gender, age, tone, description?, license?, consent?, ref_audio?}}`
- `gender`: `male`|`female`
- `age`: `teen`|`adult`|`older`|`neutral`
- `tone`: `high`|`neutral`|`deep`

Tags are optional, but **voices without a `gender` tag are never auto-cast** (they can still be assigned by hand). `ref_audio` is a path under
`voices/`, used only by backends that clone locally. `voices/<id>.wav` is the preview sample for the review page.

### Segment planning (in memory, `render.Renderer.plan`)
- Consecutive spans in one paragraph with the same voice and emotion are merged into one TTS call.
- The text is the joined `say` values, split at about `max_segment_chars` (300) on sentence boundaries.
- The title is spoken first if `speak_title` is set.
- Pause after each segment (ms, `[audio]`):

| Position | ms |
|---|---|
| within a split | 150 |
| between spans | 300 |
| end of paragraph | 650 |
| after the title | 1200 |
| scene break | 1600 |
| end of chapter | 2000 |

- **Cache key:** sha256 over
  `[backend.identity(), voice id, sha256(ref_audio)?, emotion, text, sample_rate, RENDER_VERSION]`, truncated to 32 hex characters.
  Change any of these and only the affected segments re-render.

### `audio/cache/XX/<key>.wav`
- Mono 16-bit PCM WAV at `[audio] sample_rate` (22050).
- Edge silence is trimmed to 40 ms.
- Backend output is converted to this format by `audio.to_canonical_wav`.
- Never edited after it is written.

### `audio/cache/XX/<key>.qa.json`
| Field | Type | Meaning |
|---|---|---|
| ok, wer | bool, float | the take passed if WER ≤ `qa_max_wer` (0.25), or fewer than 2 words are wrong |
| text, heard | str | expected text vs transcript |
| voice, span_ids, key | str, list[str], str | |
| variant, attempts | int | which take was kept; how many were tried |

### `audio/cache/XX/<key>.lvl.json`
`{voice, lufs}`: loudness of the segment. The per-voice gain is `clamp(voice_target_lufs − median(lufs of voice), ±max_voice_gain_db)`.

### `audio/chapters/NNNN.wav` (+ `.json`)
- The chapter's segments with per-voice gain and pauses, then a two-pass loudnorm to `target_lufs` (−19) and `true_peak_db` (−3).
- The sidecar `{fingerprint}` (hash of segment keys, pauses, gains and targets) is used to skip re-assembly.

### `audio/manifest.json` — AudioManifest
| Field | Type | Meaning |
|---|---|---|
| schema_version | int | |
| sample_rate | int | |
| chapters[] | | the rendered chapters only, in reading order; entries from earlier runs are kept for chapters outside `--chapters` |
| .chapter_id, .title | str | |
| .file | str | `audio/chapters/NNNN.wav` |
| .duration_ms, .segments | int | |

### `audio/qa_report.json`
A list of QA records (same shape as `.qa.json`) for segments still failing after retries. `[]` = clean.

**Backend contract:**
- `synthesize(text, voice: Voice|None, emotion: Emotion|None, variant: int) -> bytes` returns any WAV.
  `variant > 0` must produce a different take.
- `identity()` returns a string that changes whenever output for the same input would change.
- `NEEDS_VOICE_LIBRARY = False` for backends that don't use the library: no casting, no QA.

---

## Stage 6 — output

**Reads:**
- `audio/manifest.json` (chapter list, durations, files) and the chapter WAVs
- `meta.json`, `cover.*`
- `cast.json` (narrator voice)

**Writes:** `out/<slug><EXTENSION>`, or the `-o` path, or `<-o dir>/<slug><ext>` in batch mode.

The m4b file:
- AAC mono at `bitrate` (64k), faststart.
- Chapter markers from the manifest, in ms, laid back to back.
- Tags:

| Tag | Value |
|---|---|
| title, album | `meta.title` |
| artist, album_artist | `meta.author` |
| composer | narrator voice id |
| comment | `meta.description` |
| genre | `Audiobook` |

- Cover attached as `attached_pic`.

**Requirements for a new output method:**
- Use only the manifest, meta and cover; never re-render audio.
- Return the path written.

---

## Runner state

### `pipeline.json`
`{stage: {"method": name, "key": "<method fingerprint>:<upstream hash>"}}`
- The fingerprint is sha256 of stage, method and settings. Methods that use the LLM append the model name.
- The upstream hash is sha256 over the previous stage's `outputs()` files (names and bytes).
- A stage with `SKIP_IF_UNCHANGED` (parse, attribute) is skipped when its key matches and its outputs exist.
- `--force <stage>|all` overrides the skip.

### `llm_cache/XX/<sha256>.json`
- Key: sha256 of the full request body (model, messages, schema, sampling, thinking flag).
- Value: the validated JSON answer.
- Delete the directory to force fresh answers.

---

## Context (in memory)
| Field | Type | Meaning |
|---|---|---|
| cfg | Config | merged `incant.toml` + `incant.local.toml` + `-c` |
| source | str | CLI input (ingest only) |
| wd | WorkDir | set by ingest, or by the CLI when resuming |
| slug | str | `--slug` override |
| out / out_dir | Path | output path / batch output directory |
| chapters | set[str]\|None | `--chapters` restriction; None = all |
| log | callable | progress lines |
| results | {stage: return value} | |

## Method contract (`stages/base.py`)
```python
class Method:
    Settings: type[BaseModel] = NoSettings     # from [<stage>.<method>]; extra keys rejected
    SKIP_IF_UNCHANGED: bool = False
    def run(self, ctx: Context) -> object       # required
    @classmethod
    def outputs(cls, wd) -> list[Path]          # per stage; hashed for staleness
    def check(self) -> list[tuple[bool, str]]   # --check readiness
    def fingerprint(self) -> str                # add anything else that changes output (e.g. model)
```

## Adding things

**A method:**
1. Create `stages/<stage>/<name>/__init__.py` and `<name>.py`, holding one subclass of the stage's `Base`.
2. Write every file listed for the stage above, with the fields marked required, through `WorkDir`.
3. Keep the stage's invariants:
   - the span text invariant;
   - ids;
   - `locked`;
   - `ctx.chapters`;
   - `Span.text` is never modified.
4. Override `fingerprint()` if anything outside the settings changes the output.
5. Synthesize methods: `identity()` must change whenever output changes.
6. Quality-affecting methods: `eval/suite.py` must show no book dropping by 2 or more quotes.
7. Add a test. Discovery and `--list` pick the method up automatically.

**A stage:**
1. Create `stages/<stage>/__init__.py` with `ORDER`, `DESCRIPTION` and `Base`.
2. Give `Base.outputs()` and a `[pipeline]` default.
3. New files get a schema model with `schema_version`, `WorkDir` accessors, and an entry in this document.
4. Downstream readers must handle the file being absent, so the new stage stays optional.

**A field:**
1. Give it a default, so old work dirs still load. Readers must accept it being absent.
2. Bump `SCHEMA_VERSION` if old files would be misread.
3. If it changes output, it must reach a hashed output or cache key. Otherwise nothing will re-run.

## External services
| Service | Contract |
|---|---|
| LLM (`[llm] base_url`) | OpenAI `/v1/chat/completions` with `response_format: json_schema` (strict) and `chat_template_kwargs.enable_thinking`; `/v1/models` for `--check`. Shared 32K context (`[llm] context_tokens`). |
| TTS (IndexTTS) | `POST /v1/audio/speech` `{model, input, voice, response_format:"wav", speed?, extra_params:{lang, use_random, use_emo_text?, emo_text?, emo_alpha?}}` → WAV. A status of 4xx is fatal; 5xx and network errors are retried. |
| ffmpeg | on PATH: decode, resample, ebur128, loudnorm, AAC/mp4 mux |

## CLI
```
incant <input|slug|workdir|dir> [method…] [out.ext] [-c cfg] [-o out] [--slug S] [--from STAGE]
          [--until STAGE] [--force STAGE|all] [--chapters 1,3-5] [--dry-run] [--list] [--check]
          [--review [--host H --port P]]
```
- **Methods:**
  - `stage=method` is explicit.
  - A bare name belongs to its unique stage; a name several stages share goes to the next of them after the previous token's stage.
  - Stages left over take `[pipeline]`, then `auto`, then the stage's only method.
- **Input:** a directory without `meta.json` is a batch. Its files are processed in sorted order, failures are reported and the run continues.
- **Exit code:** non-zero on any failure.

## Review server (`--review`)
Localhost only; no authentication.

| Method | Path | Body → effect |
|---|---|---|
| GET | `/api/book` | meta, chapters (with rendered/QA status), characters, cast, voices |
| GET | `/api/chapter/NNNN` | spans with `say`, segment keys, QA |
| GET | `/api/lexicon` | book `{word: say}` |
| POST | `/api/span` | `{id, speaker?, emotion?: {text, alpha}\|null, say?: str\|null}`: span `locked=true`; `say` goes to `say_overrides.json` |
| POST | `/api/cast` | `{character, voice\|null}`: sets the role (null = back to auto); drops the character from `cast.auto` |
| POST | `/api/character` | `{id, canonical_name?, gender?, age?}`: `locked=true` |
| POST | `/api/lexicon` | `{word: say}`: rewrites `<book>/lexicon.toml` |
| GET | `/audio/segment/<key>.wav`, `/audio/chapter/NNNN.wav`, `/audio/voice/<id>.wav` | audio |

Edits take effect on the next run of the stage that reads them.

## Eval (`eval/`)
- **`gold/<book>.json`:** `{"book": slug, "spans": {span_id: label}}`. A label is one of:
  - a canonical name or alias;
  - `"narrator"`, meaning nobody speaks the words (or the first-person narrator);
  - `"~a|b"`, which matches any character whose names contain `a` or `b`.
- **`baseline.json`:** `{slug: {ok, n}}`.
- **`suite.py`:** re-attributes the gold chapters only and flags a drop of 2 or more quotes per book.
- **Adding a book:**
  1. Run ingest and parse.
  2. Label every quote in at least one chapter from the text, not from model output. Leave out lines with no single correct answer.
  3. Run `suite.py --books <slug> --save-baseline`.
- **Not yet scored:** characters, emotion, normalize, audio.
