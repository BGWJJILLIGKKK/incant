# Findings

What we have measured and learned building incant, as of 2026-11-06. Numbers are from real runs
against the hardware below; where something is a judgement rather than a measurement, it says so.

## Infrastructure

| Item | Finding |
|---|---|
| GPU | One 16 GB GPU on a separate host; llama-swap switches between the LLM and the TTS. |
| Dev VM | No GPU; 16 cores, 47 GB RAM. All CPU work (ffmpeg, whisper QA) runs here. |
| Co-residency | Qwen3.5-9B + IndexTTS-2.5 *fit* together but leave no room to work. Decision: run all LLM stages for the whole book, then let llama-swap swap to IndexTTS for synthesis. |
| Swap cost | First request after a swap: ~33 s (TTS), similar for the LLM. |

## LLM (qwen3.5-9b, Unsloth Q4_K_XL, llama.cpp)

- **Context is shared.** `n_ctx` 32768 is one KV pool across all 4 slots (unified KV), not 32K each.
  Concurrent requests whose prompt + output exceed it all fail with HTTP 500 "Context size has been
  exceeded". Fixed with client-side admission control (`TokenBudget`, `[llm] context_tokens`).
- **Throughput.** ~100 tok/s generation for a single request; prompt processing is fast. 4 parallel
  requests share the GPU (~25 tok/s each).
- **Structured output works.** `response_format: json_schema` (llama.cpp grammar) is reliable, including
  enums, `minItems`/`maxItems`, `maxLength`. Constraining speaker ids to the roster via enum prevents
  invented characters. Unbounded lists/strings let the model run away (13 of 62 extraction windows hit
  4096 tokens before limits were added; 0 after).
- **Thinking mode** (`chat_template_kwargs.enable_thinking`):
  - ~10x slower (≈5,500 tokens and 49 s for one chapter extraction vs 5 s without).
  - Loops under greedy decoding; Qwen's recommended sampling (T=0.6, top_p 0.95, top_k 20) reduces
    but does not eliminate it — some calls never converge and must be capped (we use 6–8K).
  - Much more *precise* on small judgement calls (character linking), but over-cautious.
  - Not worth it for bulk extraction or attribution.
- **A one-line "cue"/"reason" field before the answer** in the JSON schema is a cheap substitute for
  thinking: attribution 87.5% -> 93.8% on the P&P gold set.
- **Determinism.** temperature 0 + an on-disk response cache keyed by the full request makes re-runs
  free and reproducible — but only if prompts are built deterministically (results collected in
  completion order once caused cache misses and different answers).

## Character profiling (`parse/characters`)

- One-shot roster merge is the wrong shape for a 9B model: with thinking it ran out of tokens (25K);
  without, it grouped whole *families* together.
- What works: per-window extraction (no thinking) -> verify every name/alias occurs in its window ->
  pool by name -> code-proposed candidate links (shared name core or alias, compatible gender) -> one
  small LLM question per entry. Fast answer accepted only when the names corroborate it ("George
  Wickham" ⊇ "Wickham"); otherwise re-asked with thinking. Quick-only over-merged (Lady Lucas = Lady
  Catherine); thinking-only under-merged; the hybrid got both sets right.
- Same-passage co-occurrence is evidence, not a rule (true links co-occur up to 3x).
- Results: P&P main cast correct (Elizabeth/Lizzy/Eliza/Miss Bennet; Lydia = Mrs. Wickham; Charlotte =
  Mrs. Collins). Holmes harder: anthology casts and bare first names ("John") create false hubs.
- Known gaps: Kitty/Catherine split; unnamed speakers ("the housekeeper") dropped; ages judged per
  passage (Mr. Bennet "adult", not older); preface authors listed as characters.
- Cost: ~10 min per novel on this hardware.

## Attribution (`attribute/speakers`)

| Gold set | Quotes | Accuracy |
|---|---|---|
| Pride and Prejudice ch. I–II (third person) | 64 | 93.8% |
| Sherlock Holmes, "A Scandal in Bohemia" opening (first person) | 48 | 91.7% |

- Whole P&P: 1,792 quotations, 97% attributed, ~8 min.
- Error types: untagged alternation, addressee mistaken for speaker, letters/notes read aloud, quoted
  non-speech (single letters).
- First-person detection by "I/me/my" per 1000 narration words separates cleanly (P&P 0.8, Holmes 26.7);
  one LLM call names the narrator (Watson, correctly).
- Stale rosters are dangerous: with the grammar forcing a roster id, a missing character's lines go to
  someone else entirely.

## TTS (IndexTTS-2.5 over HTTP)

- Named server-side voices; emotion as free text (`extra_params.emo_text`, `emo_alpha`); `speed`.
- Output 22.05 kHz mono 16-bit WAV — used as the pipeline's working rate.
- ~3x realtime; the server processes **one request at a time** (parallel requests just queue).
  A 12-hour book ≈ 4 h of GPU.
- Clips carry ~110 ms of leading and trailing silence; trimmed to 40 ms so pauses are ours.
- Rendering a quote, its dialogue tag and the continuation as one call (when one voice reads all) gives
  better prosody than separate clips.
- Deterministic per request with `use_random: false`; `use_random: true` gives a different take (used
  for QA retries).
- Fails occasionally on very short inputs ("Bingley." garbled once in 100 segments).

## QA (faster-whisper base.en on CPU)

- 5.9x realtime on 8 threads — keeps pace with TTS without GPU time.
- Needs normalisation to avoid false failures: case/punctuation/hyphens ("To-morrow"), numbers
  ("10,000" vs "ten thousand"), and a tolerance for one misheard word in short lines.
- faster-whisper's own decoder conflicts with the installed PyAV; we decode with ffmpeg instead.

## Ingest

- Gutenberg EPUBs: chapter heading level must be chosen book-wide (a title `<h1>` in the same file as
  chapter `<h2>`s hid all headings); captions inside headings, illustration tables, image drop-caps
  ("M" + "R. BENNET") and `pg-header`/`pg-footer` boilerplate all need handling.
- Gutenberg TXT: chapter headings can sit inside `[Illustration: …]` blocks.
- Dialogue detection covers “curly” and "straight" double quotes only.

## Casting

- With 13 tagged voices, 26 speaking P&P characters had to share heavily; with 32 tagged, only one pair
  shares. Tags drive quality more than any algorithm tweak.
- Rule order that works: avoid scene partners sharing a voice > keep main characters unique > prefer
  unused voices > age match > tone match.

## Things that trip the TTS (motivation for the normalisation stage)

Observed or expected in the test books: Roman numerals in titles ("CHAPTER III.", "I. A SCANDAL IN
BOHEMIA") and regnal names; numbers and years ("1888", "10,000", "two third"); abbreviations ("Mr.",
"Dr.", "St.", "&c."); ALL-CAPS runs from drop-caps and letters ("MR. BENNET", "IRENE NORTON, *née*
ADLER") that a TTS may spell letter by letter; archaic hyphenation ("to-morrow"); long dash runs
("——"); symbols (£, &, %).

Implemented as the `normalize` stage (2026-11-06). On the test books: 788 of 3,197 P&P spans and 444 of
3,805 Holmes spans respelled; QA runs the same rules over the whisper transcript so "Mr."/"Mister" and
"1888"/"eighteen eighty-eight" aren't counted as mismatches.
