"""ffmpeg / WAV helpers. Intermediate audio is always mono 16-bit PCM WAV at cfg.audio.sample_rate."""

from __future__ import annotations

import array
import io
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not found on PATH")


def _run(args: list[str], input: bytes | None = None) -> subprocess.CompletedProcess:
    p = subprocess.run(args, input=input, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"{args[0]} failed ({p.returncode}): {p.stderr.decode(errors='replace')[-1500:]}")
    return p


def to_canonical_wav(data: bytes, rate: int) -> bytes:
    """Return mono s16 WAV at `rate`, converting with ffmpeg only if needed."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == rate:
                return data
    except (wave.Error, EOFError):
        pass  # not plain PCM WAV (float, mp3, ...) — let ffmpeg handle it
    p = _run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(rate), "-sample_fmt", "s16", "-f", "wav", "pipe:1"], input=data)
    # ffmpeg writes a streaming header with bogus sizes to a pipe; re-wrap cleanly
    with wave.open(io.BytesIO(p.stdout)) as w:
        frames = w.readframes(w.getnframes())
    return pcm_to_wav(frames, rate)


def pcm_to_wav(frames: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


def trim_silence(data: bytes, pad_ms: int = 40, threshold_db: float = -45.0) -> bytes:
    """Trim leading/trailing silence from a mono s16 WAV, keeping `pad_ms` on each side."""
    with wave.open(io.BytesIO(data)) as w:
        rate = w.getframerate()
        a = array.array("h", w.readframes(w.getnframes()))
    thr = int(32768 * 10 ** (threshold_db / 20))
    start = next((i for i, v in enumerate(a) if abs(v) > thr), None)
    if start is None:
        return data  # all silence; leave as-is
    end = next(i for i in range(len(a) - 1, -1, -1) if abs(a[i]) > thr)
    pad = rate * pad_ms // 1000
    return pcm_to_wav(a[max(0, start - pad): min(len(a), end + 1 + pad)].tobytes(), rate)


def measure_lufs(path: Path) -> float | None:
    """Integrated loudness (EBU R128) of a clip; None if it can't be measured (too short, silent, or ffmpeg failed).

    ffmpeg < 6 rejects framelog=quiet and still prints an all-zero summary, so only a successful run counts;
    it then retries with plain ebur128 and reads only the final Summary block."""
    for af in ("ebur128=framelog=quiet", "ebur128"):
        p = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-af", af, "-f", "null", "-"],
                           capture_output=True, text=True)
        summary = p.stderr.rpartition("Summary:")[2]
        m = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
        if p.returncode == 0 and m:
            v = float(m.group(1))
            return v if v > -60 else None
    return None


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path)) as w:
        return round(w.getnframes() * 1000 / w.getframerate())


def loudnorm(src: Path, dst: Path, rate: int, target_lufs: float, true_peak: float, lra: float = 11.0) -> None:
    """Two-pass EBU R128 normalization (linear mode, so no dynamic pumping)."""
    base = f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}"
    p = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", base + ":print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr.decode(errors="replace"))
    stats = json.loads(m.group(0)) if m else None
    tmp = dst.with_suffix(".tmp.wav")
    if not stats or stats["input_i"] in ("-inf", "inf"):  # silence: nothing to normalize
        shutil.copyfile(src, tmp)
    else:
        af = (
            f"{base}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}:linear=true"
        )
        _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af, "-ar", str(rate), "-ac", "1", "-sample_fmt", "s16", str(tmp)])
    tmp.replace(dst)
