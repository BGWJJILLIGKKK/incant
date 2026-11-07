from __future__ import annotations

import json
import re
from pathlib import Path

from ...normalize import slugify
from ...schemas import BookMeta
from ...workdir import WorkDir, atomic_write
from ..base import Context, Method

UNKNOWN_AUTHOR = "Unknown Author"
_UNSAFE = re.compile(r'[\x00-\x1f/\\:*?"<>|]+')


def safe_name(s: str, max_len: int = 120) -> str:
    """A readable file/directory name: path separators and characters Windows/Samba reject removed."""
    s = " ".join(_UNSAFE.sub(" ", s).split()).strip(" .")
    return s[:max_len].rstrip(" .") or "Untitled"


def book_name(meta: BookMeta) -> str:
    """The title, plus " (2)"… when ingest gave this book a numbered slug because another source has the
    same title — so two different books never share an output directory."""
    name = safe_name(meta.title)
    m = re.fullmatch(re.escape(slugify(meta.title)) + r"-(\d+)", meta.slug)
    return f"{name} ({m.group(1)})" if m else name


def tree_path(base: Path, meta: BookMeta, ext: str) -> Path:
    """<base>/<author>/<title>/<title><ext>"""
    name = book_name(meta)
    return base / safe_name(meta.author or UNKNOWN_AUTHOR) / name / f"{name}{ext}"


class OutputMethod(Method):
    """run() returns the path(s) written and records them with record(). By default the book goes to the
    output tree, <paths.output_dir>/<author>/<title>/<title><EXTENSION>; ctx.out_dir replaces the tree's base
    (batch -o DIR) and ctx.out names the file outright (-o FILE)."""

    EXTENSION = ".out"

    @staticmethod
    def record_path(wd: WorkDir) -> Path:
        return wd.root / "output.json"

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        """The record plus the files it names: the stage is stale if any of them is gone."""
        rec = cls.record_path(wd)
        try:
            files = [Path(p) for p in json.loads(rec.read_text())["files"]]
        except (OSError, ValueError, KeyError, TypeError):
            files = []
        return [rec, *files]

    def record(self, wd: WorkDir, files: list[Path]) -> None:
        atomic_write(self.record_path(wd), json.dumps({"files": [str(p.resolve()) for p in files]}, indent=2) + "\n")

    def default_path(self, ctx: Context) -> Path:
        if ctx.out:
            return ctx.out
        base = ctx.out_dir or self.cfg.paths.output_dir
        return tree_path(base, ctx.need_wd().read_meta(), self.EXTENSION)

    def run(self, ctx: Context) -> Path:
        raise NotImplementedError
