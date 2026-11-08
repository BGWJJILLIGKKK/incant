"""One file per chapter plus an .m3u playlist, in the book's directory of the output tree:
    <base>/<author>/<title>/01 - <chapter title>.<ext>, <title>.m3u
Tags per file: title = chapter title, album = book, artist = author, track = n/total; cover art (mp3, m4a).
For players without chapter support, or to listen chapter by chapter."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ....audio import _run
from ....workdir import atomic_write
from ...base import Context
from ..base import OutputMethod, cover_path, safe_name

__all__ = ["Chapters"]

CODECS = {
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-id3v2_version", "3", "-f", "mp3"]),
    "opus": (".opus", ["-c:a", "libopus", "-application", "voip", "-f", "ogg"]),
    "m4a": (".m4a", ["-c:a", "aac", "-movflags", "+faststart", "-f", "mp4"]),
}


class ChaptersSettings(BaseModel):
    format: Literal["mp3", "opus", "m4a"] = "mp3"
    bitrate: str = "64k"  # opus is fine at 32k for speech


class Chapters(OutputMethod):
    LICENSE = 'project code; FFmpeg (LGPL/GPL, a tool: output not encumbered)'
    OUTPUT_TERMS = 'none'
    Settings = ChaptersSettings
    settings: ChaptersSettings

    @property
    def EXTENSION(self) -> str:  # type: ignore[override]
        return CODECS[self.settings.format][0]

    def run(self, ctx: Context) -> list[Path]:
        wd = ctx.need_wd()
        meta = wd.read_meta()
        manifest = wd.read_manifest()
        if not manifest.chapters:
            raise RuntimeError("audio manifest has no chapters; run the synthesize stage first")
        book_file = self.default_path(ctx)  # <dir>/<title><ext>: the files go in <dir>
        folder = book_file.parent
        folder.mkdir(parents=True, exist_ok=True)
        ext, codec = CODECS[self.settings.format]
        cover = cover_path(wd) if self.settings.format in ("mp3", "m4a") else None
        width = max(2, len(str(len(manifest.chapters))))
        written: list[Path] = []
        for n, ch in enumerate(manifest.chapters, 1):
            out = folder / f"{n:0{width}d} - {safe_name(ch.title, 80)}{ext}"
            args = ["ffmpeg", "-v", "error", "-y", "-i", str(wd.root / ch.file)]
            if cover:
                args += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            tags = {"title": ch.title, "album": meta.title, "track": f"{n}/{len(manifest.chapters)}", "genre": "Audiobook"}
            if meta.author:
                tags |= {"artist": meta.author, "album_artist": meta.author}
            for k, v in tags.items():
                args += ["-metadata", f"{k}={v}"]
            tmp = out.with_name(f".{out.stem}.tmp{ext}")
            _run([*args, *codec[:2], "-b:a", self.settings.bitrate, "-ac", "1", *codec[2:], str(tmp)])
            tmp.replace(out)
            written.append(out)
        for old in folder.glob(f"*{ext}"):  # chapters that no longer exist (re-split book)
            if old not in written and old.name[:width].isdigit() and " - " in old.name:
                old.unlink()
        playlist = folder / f"{safe_name(meta.title)}.m3u"
        atomic_write(playlist, "#EXTM3U\n" + "".join(
            f"#EXTINF:{c.duration_ms // 1000},{c.title}\n{p.name}\n" for c, p in zip(manifest.chapters, written)))
        self.record(wd, [*written, playlist])
        ctx.log(f"  -> {len(written)} chapter files in {folder}")
        return written
