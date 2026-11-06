from __future__ import annotations

from pathlib import Path

from ...workdir import WorkDir
from ..base import Method


class ParseMethod(Method):
    """Must write chapters.json and chapters/*.txt; may write characters.json."""

    SKIP_IF_UNCHANGED = True

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.chapters_path, wd.root / "chapters"] + ([wd.characters_path] if wd.characters_path.exists() else [])
