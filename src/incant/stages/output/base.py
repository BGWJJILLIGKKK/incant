from __future__ import annotations

import json
import re
from pathlib import Path

from ...audio import _run
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


# ------------------------------------------------------------ shared encoding helpers


def _esc(v: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", v)


def ffmetadata(wd: WorkDir, narrator: str | None = None, chapters: bool = True) -> str:
    """ffmpeg metadata: book tags and (optionally) chapter markers from the audio manifest."""
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
    for ch in manifest.chapters if chapters else []:
        lines += ["", "[CHAPTER]", "TIMEBASE=1/1000", f"START={t}", f"END={t + ch.duration_ms}", f"title={_esc(ch.title)}"]
        t += ch.duration_ms
    return "\n".join(lines) + "\n"


def cover_path(wd: WorkDir) -> Path | None:
    meta = wd.read_meta()
    p = wd.root / meta.cover if meta.cover else None
    return p if p and p.exists() else None


def encode_book(wd: WorkDir, out: Path, codec_args: list[str], narrator: str | None = None,
                cover_args: list[str] | None = None) -> Path:
    """All chapter WAVs -> one file with book tags and chapter markers (and cover art if cover_args)."""
    manifest = wd.read_manifest()
    if not manifest.chapters:
        raise RuntimeError("audio manifest has no chapters; run the synthesize stage first")
    out.parent.mkdir(parents=True, exist_ok=True)
    concat = wd.root / "audio" / "concat.txt"
    atomic_write(concat, "".join(f"file '{(wd.root / c.file).resolve()}'\n" for c in manifest.chapters))
    metafile = wd.root / "audio" / "ffmetadata.txt"
    atomic_write(metafile, ffmetadata(wd, narrator))
    args = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(metafile)]
    cover = cover_path(wd) if cover_args is not None else None
    if cover:
        args += ["-i", str(cover)]
    args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
    if cover:
        args += ["-map", "2:v", *cover_args]
    tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
    _run([*args, *codec_args, str(tmp)])
    tmp.replace(out)
    return out
