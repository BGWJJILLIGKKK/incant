"""Voice libraries. Each synthesize backend has its own voice space; a library lists the voices in it.

A library is the backend's built-in voices (if any), then <voices_dir>/<index> ({id: {fields}}, "voice"
accepted as the id key), whose entries add voices or override built-in fields (tags, blends). The default
index, voices.json, also takes voices/<id>.json files (a Voice each), which override index entries.
"""

from __future__ import annotations

import hashlib
import json
from functools import cached_property
from pathlib import Path

from .schemas import Voice

INDEX = "voices.json"


class VoiceLibrary:
    def __init__(self, root: Path, index: str = INDEX, builtin: dict[str, Voice] | None = None):
        self.root = root
        self.index = index
        self.builtin = builtin or {}
        self._hashes: dict[str, str] = {}

    @property
    def index_path(self) -> Path:
        return self.root / self.index

    @cached_property
    def voices(self) -> dict[str, Voice]:
        out: dict[str, Voice] = {k: v.model_copy() for k, v in self.builtin.items()}
        if self.index_path.exists():
            for vid, fields in json.loads(self.index_path.read_text()).items():
                fields = {k: v for k, v in fields.items() if k != "voice"}
                base = out[vid].model_dump(exclude={"id"}) if vid in out else {}
                out[vid] = Voice(id=vid, **(base | fields))
        if self.index == INDEX and self.root.is_dir():
            for p in sorted(self.root.glob("*.json")):
                if p.name != INDEX and not p.name.startswith("_") and p.parent == self.root and _is_voice_file(p):
                    v = Voice.model_validate_json(p.read_text())
                    out[v.id] = v
        return out

    def get(self, voice_id: str | None) -> Voice | None:
        if voice_id is None:
            return None
        try:
            return self.voices[voice_id]
        except KeyError:
            raise KeyError(f"voice '{voice_id}' not found in {self.describe()}") from None

    def describe(self) -> str:
        return " + ".join(([f"{len(self.builtin)} built-in voices"] if self.builtin else []) + [str(self.index_path)])

    def ref_path(self, v: Voice) -> Path | None:
        return (self.root / v.ref_audio) if v.ref_audio else None

    def ref_sha256(self, v: Voice) -> str | None:
        p = self.ref_path(v)
        if p is None:
            return None
        if v.id not in self._hashes:
            self._hashes[v.id] = hashlib.sha256(p.read_bytes()).hexdigest()
        return self._hashes[v.id]


def _is_voice_file(p: Path) -> bool:
    """voices/<id>.json holds one Voice; other backends' indexes (kokoro.json…) are {id: fields} maps."""
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("id"), str)
