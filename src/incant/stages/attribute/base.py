from __future__ import annotations

from pathlib import Path

from ...workdir import WorkDir
from ..base import Method


class AttributeMethod(Method):
    """Must write spans/<chapter>.jsonl for every chapter (see schemas.Span)."""

    SKIP_IF_UNCHANGED = True

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.root / "spans"]
