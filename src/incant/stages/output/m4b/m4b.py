"""Chaptered m4b: AAC mono, chapter markers from the audio manifest, cover art, book tags."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from ....audio import _run
from ....schemas import NARRATOR_ID
from ....workdir import WorkDir, atomic_write
from ...base import Context
from ..base import OutputMethod

__all__ = ["M4b"]


class M4bSettings(BaseModel):
    bitrate: str = "64k"


class M4b(OutputMethod):
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


def _esc(v: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", v)


def ffmetadata(wd: WorkDir, narrator: str | None = None) -> str:
    meta = wd.read_meta()
    manifest = wd.read_manifest()
    lines = [";FFMETADATA1", f"title={_esc(meta.title)}", f"album={_esc(meta.title)}", "genre=Audiobook"]
    if meta.author:
        lines += [f"artist={_esc(meta.author)}", f"album_artist={_esc(meta.author)}"]
    if narrator:
        lines.append(f"composer={_esc(narrator)}")
    if meta.description:
        lines.append(f"comment={_esc(meta.description[:2000])}")
    t = 0
    for ch in manifest.chapters:
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={t}", f"END={t + ch.duration_ms}", f"title={_esc(ch.title)}"]
        t += ch.duration_ms
    return "\n".join(lines) + "\n"


def build_m4b(wd: WorkDir, out: Path, bitrate: str, narrator: str | None = None) -> Path:
    meta = wd.read_meta()
    manifest = wd.read_manifest()
    if not manifest.chapters:
        raise RuntimeError("audio manifest has no chapters; run the synthesize stage first")
    out.parent.mkdir(parents=True, exist_ok=True)

    concat = wd.root / "audio" / "concat.txt"
    atomic_write(concat, "".join(f"file '{(wd.root / c.file).resolve()}'\n" for c in manifest.chapters))
    metafile = wd.root / "audio" / "ffmetadata.txt"
    atomic_write(metafile, ffmetadata(wd, narrator))

    args = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(metafile)]
    cover = wd.root / meta.cover if meta.cover else None
    if cover and cover.exists():
        args += ["-i", str(cover)]
    args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if cover and cover.exists():
        args += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    tmp = out.with_suffix(".tmp.m4b")
    args += ["-c:a", "aac", "-b:a", bitrate, "-ac", "1", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    _run(args)
    tmp.replace(out)
    return out
