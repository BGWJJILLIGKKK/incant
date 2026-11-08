# Interfaces

Stages share data only through files in the book's work directory. No data passes between stages in memory except
`Context`. Every structured file is a pydantic model in `src/incant/schemas.py` and is read and written through
`WorkDir` (`src/incant/workdir.py`).

General rules for every file below:
- **Data directory:** `$INCANT_DATA`, else `INCANT_DATA=` in the `.env` beside `incant.toml` (also read by
  `./incant` and `docker compose`), else `[paths] data_dir`, else the config's directory; `~` is expanded.
  `work_dir` and `output_dir` resolve against it; `voices_dir` and the lexicon against the config. Its
  `incant.local.toml` is read last. Relative CLI inputs missing from the cwd and relative `cookies` paths
  resolve in it. Container: program in `/app`, data mounted at `/data`.
- **Location:** `<work_dir>/<slug>/`, where `work_dir` is `[paths] work_dir` (default `work/`, in the
  data directory) and `slug` is the ingested title, slugified (or `--slug`). If a *different* source already
  owns that slug, `-2`, `-3`… is appended; re-ingesting the same source (same `meta.source`) reuses its directory, whatever its slug (a changed title doesn't fork the book).
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
  - voice id: a voice of the active synthesize backend's library (its *voice space*: IndexTTS → `voices/voices.json`, Kokoro → built-in + `voices/kokoro.json`, …).

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
  cast.json               synthesize (+review, human)  Cast of the active backend
  cast.<space>.json       synthesize   casts of other backends, kept while another is active
  audio/cache/XX/<key>.wav        synthesize   one rendered segment
  audio/cache/XX/<key>.qa.json    synthesize   QA record for that segment
  audio/cache/XX/<key>.lvl.json   synthesize   loudness of that segment
  audio/chapters/NNNN.wav         synthesize   assembled chapter
  audio/chapters/NNNN.json        synthesize   assembly fingerprint
  audio/manifest.json             synthesize   AudioManifest
  audio/qa_report.json            synthesize   segments still failing QA
  audio/concat.txt, ffmetadata.txt  output     ffmpeg inputs (scratch)
  output.json                     output       {"files": [absolute paths written]}; the files are in the output tree
  removed.txt                     parse        author's notes / site text removed (metatext); absent if none
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

### Web pages (`url`) and credentials
- Title and author: the page metadata's, with a trailing `- Site` / `| Site` removed when it names the site (og:site_name or a domain label). With no author in the metadata, a trailing `by Name` becomes the author if it looks like a name (capitalized words, ≤4; or one handle in a site-templated title).
`[ingest.url]`: `user_agent`, `timeout_s`, `netrc` (default true), and `sites`: `{host: Site}`.
- A `sites` key matches that host and its subdomains; the most specific key wins.
- `Site` fields (all optional):
  - `username` + `password` | `password_env` → HTTP Basic
  - `bearer` | `bearer_env` → `Authorization: Bearer`
  - `headers` {name: value}
  - `cookies`: path to a Netscape `cookies.txt` (browser export)
- Hosts without an entry use `~/.netrc` (`$NETRC`) when `netrc = true`.
- **Where secrets go:** `incant.local.toml` (git-ignored) or environment variables (`*_env`).
- **Rules:**
  - Credentials are sent only to their own host. Redirects are followed by hand, and each hop gets its own host's credentials; cookies are scoped by domain.
  - No credentials over plain http, except to localhost.
  - Secrets are left out of the method fingerprint (`pipeline.json`), logs, errors and `meta.json`.
  - `--check` reports missing `*_env` variables and missing cookie files.
- **Errors:**
  - 401/403/407 → names the `[ingest.url.sites."host"]` entry to add, or says the credentials were rejected.
  - A page with a password field and under 300 words of text is reported as a login wall, not ingested.
  - Cookies count as credentials for the hint wording: a logged-in page without text is reported as JavaScript-loaded, not as missing cookies.
  - **Embedded-data fallback** (`embedded = true`, default): if the markup has under `min_words` words of text, the
    page's `<script>` JSON is searched for the longest string that reads as prose and has at least `embedded_min_words` (400) words.
    Nested JSON strings and HTML fragments are decoded. This covers Next.js, Nuxt, Remix and JSON-LD `articleBody`.
    It doesn't cover pages that fetch their text after loading; those need a browser.
  - **Browser fallback** (`render = "auto"`, default; `"always"`, `"never"`; `render_timeout_s` 45): if there is still too
    little text, the page is rendered in headless Chromium (Playwright, `browser` extra) and extracted again.
    - The browser gets the site's cookies with their own domains.
    - Basic/bearer/header credentials are injected per request, only into requests for the configured site; third-party requests get none.
    - Images, media and fonts are skipped.
    - About 5–10 s per page.
    - Without Playwright the error says so and the run continues without rendering.
  - A page with under `min_words` (100) words of text is refused. The error names the gate it found (age/consent/bot check,
    "enable JavaScript") or says there was no readable text, and how to supply cookies.

### Series (`url.series()`)
- `UrlIngest.series(page, fetcher) -> Series | None` is a hook: the generic `url` returns None (one page). A
  site-specific subclass returns `Series(url, title, parts=[Part(url, title)…], author, skipped)` in reading order;
  `UrlIngest.collect()` then ingests every part with the same fetcher (credentials, rendering) and writes
  `# <part title>` (series name prefix removed) + its paragraphs per part. Parts that fail are noted and skipped.
- `Ingested.source` = the series URL, so every part of a series (two bookmarks, say) is the same book (`meta.source`).
- When text comes from embedded script data, `url` also renders the page (if allowed) and keeps the longer text:
  JavaScript sites can embed reader comments while the article loads separately.

### Other formats (`ebook`, child of `epub`)
- mobi / azw / azw3 / lit / pdb (Calibre), docx / odt / rtf / fb2 / local HTML (Calibre or Pandoc; HTML
  bookmark exports excluded) are converted to EPUB in a temp dir, then read by `epub`. `converter =
  auto | calibre | pandoc`, `timeout_s`. Missing tool → error naming the apt package; DRM'd Kindle books fail.
- Docker: `--build-arg CONVERTERS=pandoc` (or `calibre`).
- Pandoc doesn't carry an `.odt`'s title/author into the EPUB: such books are named after the file.

### Scanned PDFs (`docling`, child of `pdf`; incant_local)
- Docling layout analysis + OCR (RapidOCR); table structure off. Auto-detected only for PDFs with < 200
  characters of text layer and only where Docling is installed (else `pdf` explains `ocrmypdf`); explicit
  `ingest=docling` works on any PDF. Headings that repeat (`max_repeats`, default 2) are running headers and
  dropped; image placeholders dropped. ~7 s per page on CPU. Models: `$INCANT_DOCLING_DIR` (baked in the image).

### Kindle books (`mobi`, child of `ebook`)
- `.mobi .azw .azw3 .prc .kf8`, read with the `mobi` package (KindleUnpack; extra `mobi`, in the defaults),
  no Calibre needed. KF8/AZW3 unpacks to an EPUB, read by `epub`. MOBI 6 unpacks to one HTML file in Kindle's
  old markup: large-font (`<font size>` ≥ 5) short paragraphs and `<h1-4>` become headings, `<mbp:pagebreak>`
  scene breaks, the contents table is dropped; title/author from its `content.opf`, cover from the unpacked
  images. Gutenberg boilerplate removed. Without the package, `ebook` converts these with Calibre. DRM fails.
- Gutenberg's Alice: MOBI 6 and AZW3 both give the EPUB edition's 12 chapters (~26,400 words).

### Plain text and Project Gutenberg
- `txt`: the file as is; `Title:`/`Author:` header lines name the book. `clean(raw)` is the hook for subclasses.
- `gutenberg` (inherits `txt`): picked for `.txt` files with `*** START OF THE PROJECT GUTENBERG …` in the first
  64 KB; removes the license header/footer and `[Illustration…]` placeholders (keeping a heading wrapped in one).

### Container sources (`expand`)
A method that overrides `expand(source) -> list[SourceItem] | None` is a container (today: `bookmark`).
- The CLI runs each item as its own book, exactly like a directory batch.
- Containers inside a batch directory are expanded in place.
- Duplicate sources across the batch are run once.
- Children resolve their own ingest method (`auto`, normally `url`); the container's own method token is dropped.
- `SourceItem(source, title?)`: `title` reaches ingest as `Context.title` and is used only when the source has no title of its own (empty, or equal to the URL).
- `expand` returns None for files it doesn't recognise. `load()`/`run()` on a container raise an error.

**Requirements for a new ingester:**
- `accepts(source)` must be cheap and must never claim another method's input: check the extension or URL
  scheme. Shared extensions (`.html`, `.json`) need a cheap content sniff, as bookmark does on the first 4 KB.
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
- **Author's notes / site text** (`[parse.<method>] metatext`, `stages/parse/metatext.py`): paragraphs at a chapter's
  start or end that aren't the story are removed — introductions and forewords in the author's voice, content
  notes, commentary, thanks, calls to comment/fave/vote/tip, links, update notes, site navigation, repeated title
  lines, bylines, page numbers. Listed in `removed.txt` (chapter, start/end, reason, text); no file when nothing is removed.
  - `"auto"` (default): `rules` for `source_format = "url"`, `off` for books. `"rules"`: phrase/style cues, no LLM.
    `"llm"`: the LLM labels the first/last `metatext_scan` (12) paragraphs per chapter; a label counts only if the
    paragraph also has a cue, is a title line or has ≤ 12 words. `"off"`.
  - Only blocks touching an edge go: the longest edge run that ends on a note, is ≥ ⅔ notes and has no dialogue
    among the rest, or everything before an early scene break when most of it is notes. Never text in the middle.
  - At most `metatext_max_share` (0.3) of a chapter; more is reported and nothing is removed.
  - The options count in the parse fingerprint only when changed from their defaults, so existing books aren't
    re-parsed by the option's arrival. Existing web books need `--force parse` to be cleaned.

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

**Reads:** `chapters.json`, `chapters/*.txt`, `characters.json` (required by `speakers`, used by `tags` when it came from profiling, ignored by `narrator`).

| Method | Cost | Suite accuracy (502 gold quotes) |
|---|---|---|
| `speakers` | LLM, ~8 min/book | 94.8% |
| `tags` + `parse=characters` | parse LLM only; attribution in seconds | 79.5% |
| `tags` + `parse=basic` | no LLM | ~68% (names matched loosely: it builds its own character list) |
| `narrator` | none | 3.4% (one voice) |

`tags` (rule-based): per quotation, its dialogue tag (`said Elizabeth`, `Elizabeth replied`, `said he`, `said his wife`,
`said Scrooge's nephew`, a tag ending the previous paragraph `he replied:—`), then another tag in the paragraph, then the
subject of the adjacent narration sentence (action beat), then who is addressed (`"Say, Jim, …"` → not Jim), then
turn-taking. Pronouns resolve by gender to the nearest character in the scene; roles (`wife`, `nephew`, `stranger`) to the
character whose profile description starts with that role. Delivery from the verb (`whispered` → hushed). Without a profiled
`characters.json` it writes one (`origin = "attribution"`) from tag names, gender from titles and following pronouns.
Weak spot: first-person narration with few tags (Dracula 28%).
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
- the backend's voice library (below)
- config `[audio]`, `[cast]`

| Method | Runs | Voices | Emotion | Notes |
|---|---|---|---|---|
| `indextts` | HTTP (IndexTTS-2.5 server) | `voices/voices.json` | free text + strength | best quality; GPU |
| `kokoro` | local CPU (kokoro-onnx, `uv sync --extra kokoro`); pronunciation `g2p = auto` (Misaki with context rules for heteronyms such as bow/bowed when the nlp extra is installed, else espeak) | 54 built-in (+ `voices/kokoro.json`, blends) | none | ~3–5× real time on 16 cores; model files (~340 MB) download on first use; the stage `DEFAULT` |
| `openai` | HTTP, any OpenAI `/v1/audio/speech` (Kokoro-FastAPI, openedai-speech, LocalAI, OpenAI) | built-in `kokoro` or `openai` list (+ `voices/openai.json`) | `instructions` (gpt-4o-mini-tts) if `instructions = true` | `api_key_env` |
| `elevenlabs` | HTTP, ElevenLabs API (`xi-api-key`) | 6 stock voices that work on a free tier (George, Daniel, Brian, Sarah, Lily, Alice) + account voices (`/v1/voices`, if the key has `voices_read`; labels → tags) + `voices/elevenlabs.json` | lowers `stability` | paid; 24 kHz PCM; `seed` per QA take; stops before exceeding the subscription's remaining characters unless `allow_over_quota`; a key without `user_read` can't be checked, so it stops unless `allow_over_quota` |
| `dummy` | local | any id | — | beeps; tests |

**Writes:**
- `cast.json` (and `cast.<space>.json` when switching backends)
- `audio/cache/**`, `audio/chapters/NNNN.{wav,json}`
- `audio/manifest.json`, `audio/qa_report.json`

### `cast.json` — Cast
| Field | Type | Meaning |
|---|---|---|
| schema_version | int | |
| roles | {char id: voice id} | must include `narrator` (the backend's narrator voice, below) |
| default_voice | str | voice for unknown or uncast speakers; defaults to the narrator voice |
| engine | str\|None | voice space the ids belong to (`indextts`, `kokoro`, `openai-kokoro`…). None = older file, adopted by the first backend that has all its voices. |
| auto | {char id: voice id} | the last automatic pick. A role equal to its `auto` entry is recast every run; a role that differs (hand-set) is kept. |

Casting (`casting.auto_cast`):
- Every character with at least one attributed line and no role gets a voice.
- Candidates are voices tagged `male`/`female` that match the character's gender. A character of unknown gender may take any of them. The narrator's voice is avoided where possible.
- Ranked by: fewest chapters shared with the voice's other characters, not already used by a main character, unused, best age match, best tone match, fewest lines.
- No candidate means the character falls back to `default_voice`.
- Narrator voice: the method's `narrator` setting, else `[cast] narrator` if the backend has that voice, else the backend's `DEFAULT_NARRATOR` (Kokoro `af_heart`, OpenAI `nova`).
- **Switching backends:** `cast.json` belongs to one voice space. A backend with another space moves it to `cast.<old>.json` and restores `cast.<new>.json` if present, so hand edits for each backend survive switching back and forth.
- The `[cast] main_unique` characters with the most lines never share a voice, and scene partners avoid sharing one.
- First-person books (`[cast] first_person_narration = "character"`, the default): the `narrator` role takes the narrating character's voice.

### Voice library: per backend
- `indextts`: `voices/voices.json` (+ optional `voices/<id>.json` overrides).
- `kokoro`: built-in list (`voicesets.py`), then `voices/kokoro.json` entries override fields or add voices.
- `openai`: built-in list named by `voices` (`kokoro`|`openai`|`none`), then `voices/openai.json`.

`{voice_id: {voice, source, default_emotion, gender, age, tone, description?, license?, consent?, ref_audio?, mix?, lang?}}`
- `mix`: `{voice id: weight}` — a blend of the backend's voices (Kokoro locally; sent as `a(0.7)+b(0.3)` over HTTP).
- `lang`: accent/language for backends that need it (Kokoro: `en-us`, `en-gb`, … — from the id's first letter by default).
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
- `synthesize(text, voice: Voice|None, emotion: Emotion|None, variant: int) -> bytes` returns any WAV (any rate; resampled).
  `variant > 0` must produce a different take (deterministic models: e.g. a slightly different tempo).
- `identity()` returns a string that changes whenever output for the same input would change.
- `voices() -> VoiceLibrary`: the backend's voices (default `voices/voices.json`). Tag them for casting.
- `voice_space() -> str|None`: names the id space (default: method name); None = ids are labels (no cast switching).
- `DEFAULT_NARRATOR`: a voice id to narrate when config names none of this backend's voices.
- `NEEDS_VOICE_LIBRARY = False`: any voice id accepted, no casting. `QA_SUPPORTED = False`: output can't be transcribed.
- HTTP backends: subclass `speech_http.SpeechHTTP` (auth via `api_key_env`, retries, `--check` of `/models`) and implement `payload()`; override `request()` (path + body), `decode()` (response → WAV) and `auth_headers()` for non-OpenAI APIs.
- `before_render(renderer, ctx)`: called once before synthesis; `renderer.pending_chars(chapters)` gives the uncached characters/segments (cost guards).
- Cache keys include `Voice.mix`/`lang` only when set, so adding a field never invalidates existing audio.

---

## Stage 6 — output

**Reads:**
- `audio/manifest.json` (chapter list, durations, files) and the chapter WAVs
- `meta.json`, `cover.*`
- `cast.json` (narrator voice)

**Writes:** the book into the output tree, plus `output.json` in the work dir.
- Path: `<base>/<author>/<title>/<title><EXTENSION>`. `<base>` is `[paths] output_dir` (default `output/`) or `-o DIR`; `-o FILE` names the file outright.
- `<author>` = `meta.author`, else `Unknown Author`; `<title>` = `meta.title`, plus ` (N)` when the slug was numbered (`slug-N`) for a same-titled different source.
- Names keep spaces and case; `/ \ : * ? " < > |` and control characters become spaces; ≤120 characters.
- `output.json`: `{"files": [absolute paths]}` — where the book went. An output method that sets `SKIP_IF_UNCHANGED` is up to date only if it and every listed file exist (`m4b` always rebuilds).
- New output methods: build the path with `default_path(ctx)` (or `tree_path(base, meta, ext)` for extra files in the book's directory) and call `self.record(wd, files)`.

| Method | Writes |
|---|---|
| `m4b` (stage `DEFAULT`) | `<title>.m4b`: AAC, chapters, tags, cover |
| `mp3` | `<title>.mp3`: MP3, ID3v2.3 chapter frames, tags, cover |
| `chapters` | `NN - <chapter>.{mp3,opus,m4a}` per chapter + `<title>.m3u`; per-file title/album/artist/track tags; cover for mp3/m4a |

A trailing output path picks the method by extension (`book.epub out.mp3`).

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

**`credits.txt`** (written by `OutputMethod.record`, listed in `output.json`; `credits.py`): source, each stage's
method with its `LICENSE` / `OUTPUT_TERMS`, the LLM (model, dialect) for `USES_LLM` methods, voices used (cast
roles) with `license` / `source` / `consent`, the weakest known terms (none < attribution < provider terms <
non-commercial), required attributions, and gaps (unknown terms). `[credits]` in config overrides or fills in
terms: keys `llm`, `llm.<model>`, `<stage>.<method>`, `voice.<id>`, `source`. In the book's output folder it is
`credits.txt`; beside an `-o FILE` elsewhere `<file>.credits.txt`. `--licenses` prints the per-method table and
whether a chain of known-permissive methods exists for every stage.

**Requirements for a new output method:**
- Use only the manifest, meta and cover; never re-render audio.
- Write into the output tree via `default_path(ctx)`; record every file with `self.record(wd, files)`; return the path(s).
- Shared helpers in `output/base.py`: `encode_book(wd, out, codec_args, narrator, cover_args)` (one chaptered file), `ffmetadata`, `cover_path`, `safe_name`.

---

## Runner state

### `pipeline.json`
`{stage: {"method": name, "key": "<method fingerprint>:<upstream hash>"}}`
- The fingerprint is sha256 of stage, method and settings. Methods that use the LLM append the model name.
- The upstream hash is sha256 over the previous stage's `outputs()` files (names and bytes).
- A stage with `SKIP_IF_UNCHANGED` (parse, attribute) is skipped when its key matches and its outputs exist.
- `--force <stage>|all` overrides the skip.

### Cleanup after failure (`[run] cleanup_failed`, default `"new"`)
When a run fails, `run_pipeline` deletes the book's work directory, but only if all of these hold:
- ingest created it in this run (`Context.created`: no `meta.json` existed before);
- it has no synthesized audio (`audio/cache/*/*.wav`);
- it sits directly under `work_dir`.

The error is then re-raised. In a batch each item is cleaned up on its own. Books that existed before the run (they may hold hand edits) are never deleted. Set `"never"` to keep everything.

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
| title | str | title hint from a container item (bookmark title) |
| out / out_dir | Path | `-o FILE` output file / `-o DIR` output tree base |
| chapters | set[str]\|None | `--chapters` restriction; None = all |
| log | callable | progress lines |
| results | {stage: return value} | |

## Method contract (`stages/base.py`)
```python
class Method:
    Settings: type[BaseModel] = NoSettings     # from [<stage>.<parent>…] + [<stage>.<method>]; unknown keys in its own table are an error
    SKIP_IF_UNCHANGED: bool = False
    def run(self, ctx: Context) -> object       # required
    @classmethod
    def outputs(cls, wd) -> list[Path]          # per stage; hashed for staleness
    def check(self) -> list[tuple[bool, str]]   # --check readiness
    def fingerprint(self) -> str                # add anything else that changes output (e.g. model)
    def resource(self) -> str | None            # what it waits on, for batch lanes (below)
```
`resource()` defaults to the `[llm]` server (`host:port`) for `USES_LLM` methods (and parse with
`metatext = "llm"`), else None (quick local work). Ingest methods return `"ingest"`, HTTP speech backends their
`base_url`'s `host:port`, Kokoro `"kokoro"`. A method that calls a server of its own should return that server's
address (`lanes.endpoint(url)`).

## Adding things

**A method:**
1. Create `stages/<stage>/<name>/__init__.py` and `<name>.py`, holding one subclass of the stage's `Base`.
   It may subclass another method of the same stage (`class EbookIngest(EpubIngest)`, imported from
   `..epub.epub`); only the class defined in the module counts. A child method:
   - reads `[<stage>.<parent>]` settings, then its own `[<stage>.<name>]` on top (credentials carry over);
   - wins auto-detection over its parent when both accept a source;
   - is listed under its parent in `--list`.
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
1. Create `stages/<stage>/__init__.py` with `ORDER`, `DESCRIPTION`, `Base` and (if it has several methods) `DEFAULT`.
2. Give `Base.outputs()`; optionally a `[pipeline]` default in `incant.toml`.
3. New files get a schema model with `schema_version`, `WorkDir` accessors, and an entry in this document.
4. Downstream readers must handle the file being absent, so the new stage stays optional.

**A field:**
1. Give it a default, so old work dirs still load. Readers must accept it being absent.
2. Bump `SCHEMA_VERSION` if old files would be misread.
3. If it changes output, it must reach a hashed output or cache key. Otherwise nothing will re-run.

## External services
| Service | Contract |
|---|---|
| LLM (`[llm] base_url`) | OpenAI `/v1/chat/completions`; `/v1/models` for `--check`. `dialect = "llama.cpp"` (default; also vLLM/SGLang) adds `chat_template_kwargs.enable_thinking` and top_k/min_p sampling; `"openai"` (Ollama, LM Studio, OpenRouter, OpenAI) sends standard fields only. `structured = "json_schema"` (strict, constrained) or `"json_object"` (schema in the prompt, answer validated; code fences stripped). `schema_style = "simple"`: `$defs` inlined and length/format limits dropped, for servers that reject them (Gemini); answers are still validated against the full schema. `reasoning_effort` (openai dialect) and `min_max_tokens` (a floor on every request's `max_tokens`) for reasoning models whose hidden tokens count against `max_tokens`; all three are added only when set, so other requests and their cache keys don't change. `"anthropic"`: Claude through the official SDK (`llm_anthropic.py`): structured output via `messages.parse`, adaptive thinking with `[llm] effort` / `effort_thinking`, server-side refusal fallback (`fallbacks: "default"`). Key: `api_key`, or `api_key_env` resolved by `config.secret()` (environment, then the data directory's `.env`, then the config's). Transient failures (network, 429, 5xx) retry `[llm] retries` times with backoff. Token usage is printed after each run. Shared context (`[llm] context_tokens`). |
| TTS (`openai`) | `POST /v1/audio/speech` `{model, input, voice, response_format, speed?, instructions?}` → audio. 4xx fatal; 5xx/network retried. |
| TTS (`kokoro`) | none: kokoro-onnx in-process; model files from the kokoro-onnx GitHub release into `model_dir` (`~/.cache/incant/kokoro`). |
| TTS (IndexTTS) | `POST /v1/audio/speech` `{model, input, voice, response_format:"wav", speed?, extra_params:{lang, use_random, use_emo_text?, emo_text?, emo_alpha?}}` → WAV. A status of 4xx is fatal; 5xx and network errors are retried. |
| ffmpeg | on PATH: decode, resample, ebur128, loudnorm, AAC/mp4 mux |
| Web (url ingest) | HTTP GET via httpx, manual redirects (≤ 10), per-site credentials as above; HTML → trafilatura → embedded script data → headless Chromium (Playwright) |

## CLI
```
incant <input|slug|workdir|dir> [method…] [out.ext] [-c cfg] [-o out] [--slug S] [--from STAGE]
          [--until STAGE] [--force STAGE|all] [--chapters 1,3-5] [--dry-run] [--list] [--check]
          [--review [--host H --port P]]
```
- **Methods:**
  - `stage=method` is explicit.
  - A bare name belongs to its unique stage; a name several stages share goes to the next of them after the previous token's stage.
  - A trailing output path (`out.mp3`) picks the output method writing that extension.
  - Stages left over take `[pipeline]`, then `auto`, then the stage's only method, then the stage's `DEFAULT`
    (parse `basic`, attribute `narrator`, normalize `rules`, synthesize `kokoro`, output `m4b`).
- **Input:** a directory without `meta.json` is a batch. Its files are processed in sorted order, failures are reported and the run continues.
  A container file (bookmark export) is a batch of its items; inside a directory it is expanded in place.
  With `--from` past ingest, each item's book is found by its source (`meta.json` `source`); an item never
  ingested fails.
- **Batch lanes** (`lanes.py`, `[run] lanes`): each book's stages are cut into phases where `resource()`
  changes (quick stages join a neighbouring phase), and each resource gets one worker that runs one book's phase
  at a time, in batch order. Books overlap across lanes (book 2 is attributed while book 1 is synthesized), but
  no server ever sees more than one book at once, so per-book limits (`[llm] concurrency` and its token budget,
  ElevenLabs' quota check, site pacing) still hold. Stages on the same server share a lane (a llama-swap
  serving the LLM and IndexTTS isn't made to swap between books). `"host"` (default): every server on one
  machine shares a lane, since they may share a GPU; `"endpoint"`: one lane per `host:port`; `"off"`: one book
  at a time. `--dry-run` prints the lanes; when books overlap, log lines start with `[n]`, the book's number.
- **Exit code:** non-zero on any failure.

## Review server (`--review`)
No authentication; localhost by default. Bind address: `--host`, else `$INCANT_REVIEW_HOST`, else `[review] host`
(127.0.0.1). `compose.desktop.yaml` sets `INCANT_REVIEW_HOST=0.0.0.0` inside the container and publishes the port
on the host's 127.0.0.1 only (`docker compose run --service-ports`).

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
- **`compare.py --parse P --attribute A`:** any combination on scratch copies of the gold books (the data
  directory is never written); `--parse keep` reuses each book's characters.json. Strict score (names must match
  the character list) and loose score (shared name/alias/surname; for self-built lists).
- **`g2p_ab.py --out DIR`:** Kokoro with espeak vs Misaki pronunciation, scored by whisper WER on the same
  segments; writes paired WAVs for listening (heteronyms can't be judged by WER).
- **Adding a book:**
  1. Run ingest and parse.
  2. Label every quote in at least one chapter from the text, not from model output. Leave out lines with no single correct answer.
  3. Run `suite.py --books <slug> --save-baseline`.
- **Not yet scored:** characters, emotion, normalize, audio.
