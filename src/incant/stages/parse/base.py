from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ...workdir import WorkDir
from ..base import Method


class ParseMethod(Method):
    """Must write chapters.json and chapters/*.txt; may write characters.json."""

    SKIP_IF_UNCHANGED = True

    def fingerprint(self) -> str:
        """As Method's, but the metatext options count only when changed from their defaults, so adding them
        didn't make every existing book re-parse."""
        s = self.settings
        rest = s.model_dump(mode="json", exclude={"metatext", "metatext_max_share", "metatext_scan"})
        extra = s.metatext_fingerprint() if hasattr(s, "metatext_fingerprint") else {}
        blob = json.dumps([self.stage, self.name, rest] + ([extra] if extra else []), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def llm_for_metatext(self, wd: WorkDir):
        """An LLM client when metatext = "llm" (cached per book), else None."""
        if getattr(self.settings, "metatext", None) != "llm":
            return None
        from ...llm import LLM

        return LLM(self.cfg.llm, cache_dir=wd.root / "llm_cache")

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.chapters_path, wd.root / "chapters"] + ([wd.characters_path] if wd.characters_path.exists() else [])
