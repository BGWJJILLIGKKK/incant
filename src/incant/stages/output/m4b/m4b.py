"""Chaptered m4b: AAC mono, chapter markers from the audio manifest, cover art, book tags."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ....schemas import NARRATOR_ID
from ....workdir import WorkDir
from ...base import Context
from ..base import OutputMethod, encode_book, ffmetadata  # noqa: F401 (ffmetadata re-exported)

__all__ = ["M4b"]


class M4bSettings(BaseModel):
    bitrate: str = "64k"


class M4b(OutputMethod):
    LICENSE = 'project code; FFmpeg (LGPL/GPL, a tool: output not encumbered)'
    OUTPUT_TERMS = 'none'
    Settings = M4bSettings
    settings: M4bSettings
    EXTENSION = ".m4b"

    def run(self, ctx: Context) -> Path:
        wd = ctx.need_wd()
        cast = wd.read_cast()
        narrator = cast.voice_for(NARRATOR_ID) if cast else self.cfg.cast.narrator
        out = build_m4b(wd, self.default_path(ctx), self.settings.bitrate, narrator)
        self.record(wd, [out])
        ctx.log(f"  -> {out}")
        return out


def build_m4b(wd: WorkDir, out: Path, bitrate: str, narrator: str | None = None) -> Path:
    return encode_book(wd, out, ["-c:a", "aac", "-b:a", bitrate, "-ac", "1", "-movflags", "+faststart", "-f", "mp4"],
                       narrator, cover_args=["-c:v", "copy", "-disposition:v:0", "attached_pic"])
