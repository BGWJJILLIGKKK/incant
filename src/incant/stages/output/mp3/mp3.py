"""One chaptered MP3 (ID3v2 chapter frames, tags, cover art) — for players that don't take m4b."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from ....schemas import NARRATOR_ID
from ...base import Context
from ..base import OutputMethod, encode_book

__all__ = ["Mp3"]


class Mp3Settings(BaseModel):
    bitrate: str = "64k"  # mono speech


class Mp3(OutputMethod):
    Settings = Mp3Settings
    settings: Mp3Settings
    EXTENSION = ".mp3"

    def run(self, ctx: Context) -> Path:
        wd = ctx.need_wd()
        cast = wd.read_cast()
        narrator = cast.voice_for(NARRATOR_ID) if cast else None
        out = encode_book(wd, self.default_path(ctx),
                          ["-c:a", "libmp3lame", "-b:a", self.settings.bitrate, "-ac", "1", "-id3v2_version", "3", "-f", "mp3"],
                          narrator, cover_args=["-c:v", "copy", "-metadata:s:v", "title=Album cover",
                                                "-metadata:s:v", "comment=Cover (front)"])
        self.record(wd, [out])
        ctx.log(f"  -> {out}")
        return out
