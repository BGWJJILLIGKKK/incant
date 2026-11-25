# Contributing

Bug reports, fixes and new methods are welcome. Open an issue first for anything large (a new stage, a new
dependency, a change to a file format), so the design can be agreed on before you write it.

## Setup

```sh
git clone https://github.com/BGWJJILLIGKKK/incant.git && cd incant
uv sync --extra qa --extra kokoro --extra nlp --extra mobi --extra anthropic   # what the tests exercise
uv run pytest
```

Tests that need an optional extra, ffmpeg or downloaded models skip themselves when those are missing; CI runs
them with ffmpeg and the extras above. Docling tests need `uv sync --extra torch` (large).

## Before opening a pull request

- `uv run pytest` passes, and new behaviour has a test.
- `uvx ruff check --select E9,F src tests eval` is clean.
- **Speaker attribution changes** (`attribute/*`, `parse/*`, prompts, `characters.json` handling): run
  `uv run python eval/suite.py` (or `--attribute tags` without an LLM) before and after, and put both results
  in the pull request. A gain on one book that costs another isn't a gain; the suite flags any book that drops
  by 2+ quotes.
- **Defaults stay runnable without a server:** the default pipeline must keep working with only what incant
  bundles (CPU, no LLM, no GPU).
- Docs follow the code: `README.md` for users, `docs/interfaces.md` for contracts, settings and file formats,
  and a line in `CHANGELOG.md` under "Unreleased" (add that heading above the latest release if it isn't there).

## Design rules

- Stages share data only through files in the work directory (`docs/interfaces.md`).
- The LLM labels spans; it never writes book text.
- Casting is separate from attribution; audio stays lossless until the output stage.
- A method is a directory `src/incant/stages/<stage>/<name>/<name>.py`, found by name; it may inherit from
  another method of its stage (`docs/interfaces.md`: "Adding things").
- Declare a method's `LICENSE` / `OUTPUT_TERMS` only when they are certain; unknown is reported, never guessed.
- New dependencies go in an optional extra unless every user needs them; note their license in
  `THIRD_PARTY_NOTICES.md` (`tools/licenses.py` lists what's installed).

## What not to commit

- Copyrighted text or audio, including in test fixtures; use public-domain texts or text you wrote.
- Secrets, cookies, or your `incant.local.toml` / `.env`.
- Voices you don't have the right to distribute.

## License

By contributing, you agree that your contributions are licensed under the project's BSD 3-Clause license
([LICENSE](LICENSE)).
