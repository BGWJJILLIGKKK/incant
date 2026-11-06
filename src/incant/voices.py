"""Voice library: <voices_dir>/<id>.json (a Voice) next to its reference audio."""

from __future__ import annotations

import hashlib
import json
from functools import cached_property
from pathlib import Path

from .schemas import Voice


INDEX = "voices.json"


class VoiceLibrary:
    """Voices from voices/voices.json ({id: {fields}}, "voice" accepted as the id key) plus any
    voices/<id>.json files, which override index entries of the same id."""

    def __init__(self, root: Path):
        self.root = root
        self._hashes: dict[str, str] = {}

    @cached_property
    def voices(self) -> dict[str, Voice]:
        out: dict[str, Voice] = {}
        index = self.root / INDEX
        if index.exists():
            for vid, fields in json.loads(index.read_text()).items():
                fields = {k: v for k, v in fields.items() if k != "voice"}
                out[vid] = Voice(id=vid, **fields)
        if self.root.is_dir():
            for p in sorted(self.root.glob("*.json")):
                if p.name != INDEX:
                    v = Voice.model_validate_json(p.read_text())
                    out[v.id] = v
        return out

    def get(self, voice_id: str | None) -> Voice | None:
        if voice_id is None:
            return None
        try:
            return self.voices[voice_id]
        except KeyError:
            raise KeyError(f"voice '{voice_id}' not found in {self.root}") from None

    def ref_path(self, v: Voice) -> Path | None:
        return (self.root / v.ref_audio) if v.ref_audio else None

    def ref_sha256(self, v: Voice) -> str | None:
        p = self.ref_path(v)
        if p is None:
            return None
        if v.id not in self._hashes:
            self._hashes[v.id] = hashlib.sha256(p.read_bytes()).hexdigest()
        return self._hashes[v.id]
