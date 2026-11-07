"""Stage 4: spans + cast -> per-segment cached WAVs -> per-chapter normalized WAVs + manifest.

Segments are content-addressed (backend identity, voice + its reference audio,
emotion, text, sample rate), so editing one line or recasting one character
re-renders only the affected segments. Chapters are independent, which is what
lets attribution of chapter N+1 overlap rendering of chapter N.
"""

from __future__ import annotations

import hashlib
import json
import wave
from collections.abc import Callable
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from typing import TYPE_CHECKING

from ...audio import loudnorm, to_canonical_wav, trim_silence, wav_duration_ms
from ...config import Config
from ...normalize import is_scene_break
from ...qa import Transcriber
from ...schemas import NARRATOR_ID, AudioManifest, Cast, Chapter, ChapterAudio, Emotion, Segment, Voice
from ...textprep import chunk
from ...voices import VoiceLibrary
from ...workdir import WorkDir, atomic_write

if TYPE_CHECKING:
    from .base import SynthesizeMethod

# Bump when post-processing of synthesized clips changes (invalidates the segment cache).
RENDER_VERSION = 2  # 2: trim clip edge silence
TRIM_PAD_MS = 40

Progress = Callable[[str], None]


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
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]

    def _groups(self, ch: Chapter) -> list[_Group | None]:
        """Merge consecutive spans in a paragraph that would be read by the same voice with the
        same emotion, so e.g. quote + dialogue tag + quote is one TTS call with natural prosody
        when a single voice reads all three. None marks a scene break."""
        out: list[_Group | None] = []
        for s in self.wd.read_spans(ch.id):
            if is_scene_break(s.text):
                out.append(None)
                continue
            emo = s.emotion.model_dump() if s.emotion else None
            key = (s.para, self.cast.voice_for(s.speaker), json.dumps(emo, sort_keys=True))
            last = out[-1] if out else None
            if last is not None and last.key == key:
                last.text += s.text
                last.span_ids.append(s.id)
            else:
                out.append(_Group(key, s.para, s.speaker, s.emotion, s.text, [s.id]))
        return out

    def plan(self, ch: Chapter) -> list[Segment]:
        a = self.cfg.audio
        segs: list[Segment] = []

        def add(span_ids: list[str], text: str, speaker: str | None, emotion, pause_ms: int):
            voice = self._voice(speaker)
            segs.append(Segment(span_ids=span_ids, text=text, voice=voice.id if voice else None, emotion=emotion,
                                cache_key=self._key(text, voice, emotion), pause_after_ms=pause_ms))

        if ch.speak_title:
            for t in chunk(ch.title, self.backend.settings.max_segment_chars):
                add([f"{ch.id}-title"], t, NARRATOR_ID, None, a.pause_title_ms)

        groups = self._groups(ch)
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

    # ------------------------------------------------------------ synthesis

    def _cache_path(self, key: str) -> Path:
        return self.wd.cache_dir / key[:2] / f"{key}.wav"

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
            return rec if rec and not rec["ok"] else None
        st = self.backend.settings
        best: tuple[float, bytes, dict] | None = None
        attempts = 1 + (st.qa_retries if self.qa else 0)
        for variant in range(attempts):
            wav = path.read_bytes() if variant == 0 and path.exists() else self._render_take(seg, variant)
            if self.qa is None:
                atomic_write(path, wav)
                return None
            chk = self.qa.check(wav, seg.text)
            rec = {"ok": chk.ok(st.qa_max_wer), "wer": round(chk.wer, 3), "heard": chk.heard, "text": seg.text,
                   "voice": seg.voice, "variant": variant, "span_ids": seg.span_ids}
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

    def assemble(self, ch: Chapter, segs: list[Segment]) -> ChapterAudio:
        a = self.cfg.audio
        out = self.wd.chapter_audio_dir / f"{ch.id}.wav"
        sidecar = out.with_suffix(".json")
        fingerprint = hashlib.sha256(
            json.dumps([[s.cache_key, s.pause_after_ms] for s in segs] + [a.target_lufs, a.true_peak_db]).encode()
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
            for s in segs:
                with wave.open(str(self._cache_path(s.cache_key))) as r:
                    w.writeframes(r.readframes(r.getnframes()))
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
        results: list[ChapterAudio] = []
        qa_fail: list[dict] = []
        for ch in chapters:
            if only is not None and ch.id not in only:
                if ch.id in prior:
                    results.append(prior[ch.id])
                continue
            segs = self.plan(ch)
            if progress:
                progress(f"chapter {ch.id} '{ch.title}': {len(segs)} segments")
            qa_fail += self.synthesize(segs, progress)
            results.append(self.assemble(ch, segs))
        manifest = AudioManifest(sample_rate=self.cfg.audio.sample_rate, chapters=results)
        self.wd.write_manifest(manifest)
        if self.qa is not None:
            report = self.wd.root / "audio" / "qa_report.json"
            atomic_write(report, json.dumps(qa_fail, indent=1) + "\n")
            if progress:
                progress(f"  QA: {len(qa_fail)} segments still mismatched after retries" + (f" -> {report}" if qa_fail else ""))
        return manifest
