"""Stage 5 (synthesize): speech (spans with `say`) + cast -> per-segment cached WAVs -> per-chapter normalized WAVs + manifest.

Segments are content-addressed (backend identity, voice + its reference audio,
emotion, text, sample rate), so editing one line or recasting one character
re-renders only the affected segments. Chapters are rendered independently, so
--chapters can re-render a subset.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import tomllib
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from typing import TYPE_CHECKING

from ...audio import loudnorm, measure_lufs, to_canonical_wav, trim_silence, wav_duration_ms
from ...config import Config
from ...normalize import is_scene_break
from ...qa import Transcriber, compare, words
from ...schemas import NARRATOR_ID, AudioManifest, Cast, Chapter, ChapterAudio, ChapterSpeech, Emotion, Segment, Span, Voice
from ...textprep import chunk
from ...voices import VoiceLibrary
from ...workdir import WorkDir, atomic_write

if TYPE_CHECKING:
    from .base import SynthesizeMethod

# Bump when post-processing of synthesized clips changes (invalidates the segment cache).
RENDER_VERSION = 2  # 2: trim clip edge silence
TRIM_PAD_MS = 40

Progress = Callable[[str], None]


def apply_gain(frames: bytes, db: float) -> bytes:
    """Scale 16-bit PCM by `db`, reduced if needed so the clip's peak stays below -1 dBFS."""
    import numpy as np

    x = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
    if not len(x):
        return frames
    g = 10 ** (db / 20)
    peak = float(np.abs(x).max()) * g
    ceiling = 32767 * 10 ** (-1 / 20)
    if peak > ceiling:
        g *= ceiling / peak
    return np.clip(x * g, -32768, 32767).astype(np.int16).tobytes()


@dataclass
class _Group:
    key: tuple
    para: int
    speaker: str | None
    emotion: Emotion | None
    text: str
    span_ids: list[str]


class Renderer:
    def __init__(self, cfg: Config, wd: WorkDir, backend: SynthesizeMethod, voices: VoiceLibrary, cast: Cast, strict_voices: bool = True,
                 qa: Transcriber | None = None):
        self.cfg, self.wd, self.backend, self.voices, self.cast = cfg, wd, backend, voices, cast
        self.strict_voices = strict_voices
        self.backend_id = backend.identity()
        self._resolved: dict[str, Voice] = {}  # voice id -> Voice, filled during planning
        self.qa = qa
        self._whitelist = self._build_whitelist()

    def _build_whitelist(self) -> set[str]:
        wl: set[str] = set()
        chars = self.wd.read_characters()
        if chars:
            for c in chars.characters:
                for n in (c.canonical_name, *c.aliases):
                    wl.update(words(n))
        for p in (self.wd.root / "lexicon.toml", Path(self.cfg.resolve(Path("lexicon.toml")))):
            if p.exists():
                try:
                    entries = tomllib.loads(p.read_text()).get("words", {})
                    for k, v in entries.items():
                        wl.update(words(str(k)))
                        wl.update(words(str(v)))
                except Exception:
                    pass
        return wl

    # ------------------------------------------------------------ planning

    def _voice(self, speaker: str | None) -> Voice | None:
        vid = self.cast.voice_for(speaker)
        if vid is None:
            return None
        if vid not in self._resolved:
            if vid in self.voices.voices or self.strict_voices:
                self._resolved[vid] = self.voices.get(vid)  # raises if missing
            else:
                self._resolved[vid] = Voice(id=vid)  # placeholder for library-free backends (dummy)
        return self._resolved[vid]

    def _key(self, text: str, voice: Voice | None, emotion) -> str:
        parts = [
            self.backend_id,
            voice.id if voice else None,
            self.voices.ref_sha256(voice) if voice and voice.ref_audio else None,
            emotion.model_dump() if emotion else None,
            text,
            self.cfg.audio.sample_rate,
            RENDER_VERSION,
        ]
        if voice and (voice.mix or voice.lang):  # only when set, so existing cache keys stay valid
            parts.append({"mix": voice.mix, "lang": voice.lang})
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]

    def _groups(self, spans: list[Span]) -> list[_Group | None]:
        """Merge consecutive spans in a paragraph that would be read by the same voice with the
        same emotion, so e.g. quote + dialogue tag + quote is one TTS call with natural prosody
        when a single voice reads all three. None marks a scene break."""
        out: list[_Group | None] = []
        for s in spans:
            if s.omitted:
                continue
            if is_scene_break(s.text):
                out.append(None)
                continue
            if s.say == "":
                continue
            emo = s.emotion.model_dump() if s.emotion else None
            key = (s.para, self.cast.voice_for(s.speaker), json.dumps(emo, sort_keys=True))
            last = out[-1] if out else None
            words = s.say if s.say is not None else s.text  # `say` is trimmed: join pieces with a space
            if last is not None and last.key == key:
                last.text += (" " if s.say is not None else "") + words
                last.span_ids.append(s.id)
            else:
                out.append(_Group(key, s.para, s.speaker, s.emotion, words, [s.id]))
        return out

    def plan(self, ch: Chapter, *, speech: ChapterSpeech | None = None) -> list[Segment]:
        a = self.cfg.audio
        segs: list[Segment] = []
        if speech is None:
            speech = self.wd.read_speech(ch.id)

        def add(span_ids: list[str], text: str, speaker: str | None, emotion, pause_ms: int):
            voice = self._voice(speaker)
            segs.append(Segment(span_ids=span_ids, text=text, voice=voice.id if voice else None, emotion=emotion,
                                cache_key=self._key(text, voice, emotion), pause_after_ms=pause_ms))

        if ch.speak_title:
            for t in chunk(speech.title_say if speech else ch.title, self.backend.settings.max_segment_chars):
                add([f"{ch.id}-title"], t, NARRATOR_ID, None, a.pause_title_ms)

        groups = self._groups(speech.spans if speech else self.wd.read_spans(ch.id))
        for g, nxt in zip(groups, [*groups[1:], None]):
            if g is None:  # scene break
                if segs:
                    segs[-1].pause_after_ms = max(segs[-1].pause_after_ms, a.pause_scene_break_ms)
                continue
            pieces = chunk(g.text, self.backend.settings.max_segment_chars)
            last_in_para = nxt is None or nxt.para != g.para
            for j, t in enumerate(pieces):
                if j + 1 < len(pieces):
                    pause = a.pause_segment_ms
                else:
                    pause = a.pause_paragraph_ms if last_in_para else a.pause_span_ms
                add(g.span_ids, t, g.speaker, g.emotion, pause)
        if segs:
            segs[-1].pause_after_ms = a.pause_chapter_end_ms
        return segs

    def pending_chars(self, only: set[str] | None = None) -> tuple[int, int]:
        """(characters, segments) still to synthesize for these chapters (not in the segment cache)."""
        chars = segs = 0
        for ch in self.wd.read_chapters().chapters:
            if only is not None and ch.id not in only:
                continue
            for s in self.plan(ch):
                if not self._cache_path(s.cache_key).exists():
                    chars, segs = chars + len(s.text), segs + 1
        return chars, segs

    # ------------------------------------------------------------ synthesis

    def _cache_path(self, key: str) -> Path:
        return self.wd.cache_dir / key[:2] / f"{key}.wav"

    def _lvl_path(self, key: str) -> Path:
        return self.wd.cache_dir / key[:2] / f"{key}.lvl.json"

    def measure(self, segs: list[Segment]) -> None:
        """Loudness of each segment's clip, cached beside it (with the voice, for per-voice statistics)."""
        def stale(key: str) -> bool:  # missing, or the 0.0 an old ffmpeg failure left behind (speech is always < 0 LUFS)
            p = self._lvl_path(key)
            return not p.exists() or json.loads(p.read_text()).get("lufs") == 0.0

        todo = [s for s in segs if self._cache_path(s.cache_key).exists() and stale(s.cache_key)]

        def one(s: Segment):
            atomic_write(self._lvl_path(s.cache_key),
                         json.dumps({"voice": s.voice, "lufs": measure_lufs(self._cache_path(s.cache_key))}) + "\n")

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(one, todo))

    def voice_gains(self, segs: Iterable[Segment], partial: bool = False) -> dict[str, float]:
        """Match voices using measured clips from the current render plan."""
        a = self.cfg.audio
        baseline_path = self.wd.root / "audio" / "gain_baseline.json"
        scope = hashlib.sha256(json.dumps(
            [self.backend_id, self.cast.roles, self.cast.default_voice, a.sample_rate,
             a.voice_target_lufs, a.max_voice_gain_db],
            sort_keys=True,
        ).encode()).hexdigest()
        gains: dict[str, float] = {}
        if partial and baseline_path.exists():
            try:
                saved = json.loads(baseline_path.read_text())
                if saved.get("scope") == scope:
                    gains = {voice: float(db) for voice, db in saved["gains"].items()}
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
        levels: dict[str, list[float]] = {}
        active = {s.cache_key: s.voice for s in segs if s.voice and s.voice not in gains}
        for key, voice in active.items():
            path = self._lvl_path(key)
            if path.exists():
                level = json.loads(path.read_text()).get("lufs")
                if isinstance(level, (int, float)) and level < 0:
                    levels.setdefault(voice, []).append(level)
        for voice, samples in levels.items():
            median = statistics.median(samples)
            gains[voice] = round(max(-a.max_voice_gain_db, min(a.max_voice_gain_db, a.voice_target_lufs - median)), 1)
        if not partial or levels:
            atomic_write(baseline_path, json.dumps({"scope": scope, "gains": gains}, indent=2) + "\n")
        return gains

    def _qa_path(self, key: str) -> Path:
        return self.wd.cache_dir / key[:2] / f"{key}.qa.json"

    def _render_take(self, seg: Segment, variant: int) -> bytes:
        voice = self._resolved.get(seg.voice) if seg.voice else None
        raw = self.backend.synthesize(seg.text, voice, seg.emotion, variant=variant)
        return trim_silence(to_canonical_wav(raw, self.cfg.audio.sample_rate), pad_ms=TRIM_PAD_MS)

    def _synth_one(self, seg: Segment) -> dict | None:
        """Render (or reuse) a segment; with QA on, verify it and retry bad takes, keeping the best.
        Returns the QA record if the final take still fails."""
        path, qa_path = self._cache_path(seg.cache_key), self._qa_path(seg.cache_key)
        if path.exists() and (self.qa is None or qa_path.exists()):
            rec = json.loads(qa_path.read_text()) if qa_path.exists() else None
            if rec and not rec["ok"]:  # re-score the stored transcript: the comparison may have improved
                chk = compare(rec["text"], rec["heard"], whitelist=self._whitelist)
                if chk.ok(self.backend.settings.qa_max_wer):
                    rec.update(ok=True, wer=round(chk.wer, 3))
                    atomic_write(qa_path, json.dumps(rec) + "\n")
            return rec if rec and not rec["ok"] else None
        st = self.backend.settings
        best: tuple[float, bytes, dict] | None = None
        attempts = 1 + (st.qa_retries if self.qa else 0)
        for variant in range(attempts):
            wav = path.read_bytes() if variant == 0 and path.exists() else self._render_take(seg, variant)
            if self.qa is None:
                atomic_write(path, wav)
                return None
            try:
                chk = self.qa.check(wav, seg.text, whitelist=self._whitelist)
            except TypeError:
                chk = self.qa.check(wav, seg.text)
            rec = {"ok": chk.ok(st.qa_max_wer), "wer": round(chk.wer, 3), "heard": chk.heard, "text": seg.text,
                   "voice": seg.voice, "variant": variant, "span_ids": seg.span_ids, "key": seg.cache_key}
            if best is None or chk.wer < best[0]:
                best = (chk.wer, wav, rec)
            if rec["ok"]:
                break
        _, wav, rec = best
        rec["attempts"] = attempts if not rec["ok"] else rec["variant"] + 1
        atomic_write(path, wav)
        atomic_write(qa_path, json.dumps(rec) + "\n")
        return None if rec["ok"] else rec

    def synthesize(self, segs: list[Segment], progress: Progress | None = None) -> list[dict]:
        """Returns QA failures (segments whose best take still doesn't match its text)."""
        todo = {s.cache_key: s for s in segs
                if not self._cache_path(s.cache_key).exists() or (self.qa and not self._qa_path(s.cache_key).exists())}
        failures = [r for s in segs if s.cache_key not in todo and (r := self._synth_one(s))]
        if not todo:
            return failures
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, self.backend.settings.concurrency)) as ex:
            futs = [ex.submit(self._synth_one, s) for s in todo.values()]
            for f in as_completed(futs):
                if rec := f.result():
                    failures.append(rec)
                done += 1
                if progress and (done % 25 == 0 or done == len(todo)):
                    progress(f"  synthesized {done}/{len(todo)}" + (f" ({len(failures)} failed QA)" if failures else ""))
        return failures

    # ------------------------------------------------------------ assembly

    def assemble(self, ch: Chapter, segs: list[Segment], gains: dict[str, float] | None = None) -> ChapterAudio:
        a = self.cfg.audio
        gains = gains or {}
        out = self.wd.chapter_audio_dir / f"{ch.id}.wav"
        sidecar = out.with_suffix(".json")
        empty_pause = [] if segs else [a.pause_chapter_end_ms]
        fingerprint = hashlib.sha256(
            json.dumps([[s.cache_key, s.pause_after_ms, gains.get(s.voice or "", 0.0)] for s in segs]
                       + [a.target_lufs, a.true_peak_db] + empty_pause).encode()
        ).hexdigest()
        if out.exists() and sidecar.exists() and json.loads(sidecar.read_text()).get("fingerprint") == fingerprint:
            return ChapterAudio(chapter_id=ch.id, title=ch.title, file=str(out.relative_to(self.wd.root)),
                                duration_ms=wav_duration_ms(out), segments=len(segs))

        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_suffix(".raw.wav")
        rate = a.sample_rate
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            if not segs:
                w.writeframes(b"\x00\x00" * max(1, rate * a.pause_chapter_end_ms // 1000))
            for s in segs:
                with wave.open(str(self._cache_path(s.cache_key))) as r:
                    frames = r.readframes(r.getnframes())
                g = gains.get(s.voice or "", 0.0)
                w.writeframes(apply_gain(frames, g) if g else frames)
                w.writeframes(b"\x00\x00" * (rate * s.pause_after_ms // 1000))
        loudnorm(raw, out, rate, a.target_lufs, a.true_peak_db)
        raw.unlink()
        atomic_write(sidecar, json.dumps({"fingerprint": fingerprint}) + "\n")
        return ChapterAudio(chapter_id=ch.id, title=ch.title, file=str(out.relative_to(self.wd.root)),
                            duration_ms=wav_duration_ms(out), segments=len(segs))

    # ------------------------------------------------------------ driver

    def render(self, only: set[str] | None = None, progress: Progress | None = None) -> AudioManifest:
        chapters = self.wd.read_chapters().chapters
        prior = {c.chapter_id: c for c in self.wd.read_manifest().chapters} if self.wd.manifest_path.exists() else {}
        plans: dict[str, list[Segment]] = {}
        qa_fail: list[dict] = []
        for ch in chapters:
            if only is not None and ch.id not in only:
                continue
            segs = self.plan(ch)
            plans[ch.id] = segs
            if progress:
                progress(f"chapter {ch.id} '{ch.title}': {len(segs)} segments")
            qa_fail += self.synthesize(segs, progress)
            if self.cfg.audio.level_match:
                self.measure(segs)
        gains = {}
        if self.cfg.audio.level_match and plans:
            gains = self.voice_gains((s for segs in plans.values() for s in segs), partial=len(plans) < len(chapters))
            if progress and gains:
                progress("  level match (dB): " + ", ".join(f"{v} {db:+.1f}" for v, db in sorted(gains.items())))
        results = []
        for ch in chapters:
            if ch.id in plans:
                results.append(self.assemble(ch, plans[ch.id], gains))
            elif ch.id in prior:
                results.append(prior[ch.id])
        manifest = AudioManifest(sample_rate=self.cfg.audio.sample_rate, chapters=results)
        self.wd.write_manifest(manifest)
        if self.qa is not None:
            report = self.wd.root / "audio" / "qa_report.json"
            atomic_write(report, json.dumps(qa_fail, indent=1) + "\n")
            if progress:
                progress(f"  QA: {len(qa_fail)} segments still mismatched after retries" + (f" -> {report}" if qa_fail else ""))
        return manifest
