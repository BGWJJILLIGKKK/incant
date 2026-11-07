from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...schemas import ChapterSpeech, Span, SpanKind
from ...workdir import WorkDir
from ..base import Context, Method


class NormalizeSettings(BaseModel):
    # Pronunciation lexicon: TOML with a [words] table, e.g.  Hermione = "Her-my-oh-nee"
    # The project-wide file plus <work>/<book>/lexicon.toml (the book's entries win). Whole words, case-insensitive.
    lexicon: str = "lexicon.toml"
    show_examples: int = 12  # changed spans to print as a sample


class Lexicon:
    def __init__(self, entries: dict[str, str]):
        self.entries = entries
        self.pattern = (re.compile(r"(?<![\w'’])(" + "|".join(re.escape(k) for k in sorted(entries, key=len, reverse=True)) + r")(?![\w'’])", re.I)
                        if entries else None)
        self._lower = {k.casefold(): v for k, v in entries.items()}

    @classmethod
    def load(cls, *paths: Path) -> Lexicon:
        entries: dict[str, str] = {}
        for p in paths:
            if p and p.exists():
                entries |= {str(k): str(v) for k, v in tomllib.loads(p.read_text()).get("words", {}).items()}
        return cls(entries)

    def apply(self, text: str) -> str:
        return self.pattern.sub(lambda m: self._lower[m.group(0).casefold()], text) if self.pattern else text


class NormalizeMethod(Method):
    """Implement `say(text, kind, title=False)`; the base handles files, the lexicon and reporting."""

    Settings: ClassVar[type[BaseModel]] = NormalizeSettings
    settings: NormalizeSettings

    def say(self, text: str, kind: SpanKind | None, title: bool = False) -> str:
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.root / "speech"]

    def lexicon(self, wd: WorkDir) -> Lexicon:
        return Lexicon.load(self.cfg.resolve(Path(self.settings.lexicon)), wd.root / "lexicon.toml")

    def run(self, ctx: Context):
        wd = ctx.need_wd()
        lex = self.lexicon(wd)
        changed, total, examples, report = 0, 0, [], []
        for ch in wd.read_chapters().chapters:
            spans: list[Span] = wd.read_spans(ch.id)
            for s in spans:
                s.say = lex.apply(self.say(s.text, s.kind))
                total += 1
                plain = " ".join(s.text.replace("*", "").strip("“”\" ").split())
                if s.say and s.say != plain and _interesting(plain, s.say):
                    changed += 1
                    regions = _diff_windows(plain, s.say)
                    report.append(s.id + "\n" + "".join(f"  - {x}\n  + {y}\n" for x, y in regions))
                    if len(examples) < self.settings.show_examples:
                        examples.append(regions[0])
            title_say = lex.apply(self.say(ch.title, None, title=True))
            wd.write_speech(ChapterSpeech(chapter_id=ch.id, title_say=title_say, spans=spans))
        (wd.root / "speech" / "changes.txt").write_text("".join(report))
        ctx.log(f"  {changed:,} of {total:,} spans respelled (all listed in speech/changes.txt); "
                f"lexicon: {len(lex.entries)} entries")
        for before, after in examples:
            ctx.log(f"    {before}\n      -> {after}")
        return changed


def _diff_windows(before: str, after: str, context: int = 4) -> list[tuple[str, str]]:
    """Each changed region with a few words either side, e.g. ("…of March, 1888—I was…", "…eighteen…")."""
    import difflib

    a, b = before.split(), after.split()
    ops = [op for op in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes() if op[0] != "equal"]
    fmt = lambda w, lo, hi: ("…" if lo else "") + " ".join(w[lo:hi]) + ("…" if hi < len(w) else "")  # noqa: E731
    out = []
    for _, i1, i2, j1, j2 in ops:
        out.append((fmt(a, max(0, i1 - context), min(len(a), i2 + context)),
                    fmt(b, max(0, j1 - context), min(len(b), j2 + context))))
    return out or [(before, after)]


def _interesting(before: str, after: str) -> bool:
    """Skip examples that only differ by punctuation/markup stripping."""
    norm = lambda s: re.sub(r"[^\w]+", " ", s).strip().casefold()  # noqa: E731
    return norm(before) != norm(after)
