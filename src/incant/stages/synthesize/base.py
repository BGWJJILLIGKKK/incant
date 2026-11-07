from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...schemas import NARRATOR_ID, AudioManifest, Cast, Emotion, Voice
from ...casting import auto_cast
from ...voices import VoiceLibrary
from ...workdir import WorkDir
from ..base import Context, Method
from .render import Renderer


class SynthSettings(BaseModel):
    concurrency: int = 1
    max_segment_chars: int = 300
    # Speech-to-text check of every segment (needs `uv sync --extra qa`; skipped if not installed).
    qa: bool = True
    qa_model: str = "base.en"
    qa_threads: int = 8
    qa_max_wer: float = 0.25  # word error rate above this (and >= 2 wrong words) fails a take
    qa_retries: int = 2  # extra takes for a failing segment; the best one is kept


class SynthesizeMethod(Method):
    Settings: ClassVar[type[BaseModel]] = SynthSettings
    settings: SynthSettings

    # False for backends that don't need reference clips (voice ids are just labels).
    NEEDS_VOICE_LIBRARY: ClassVar[bool] = True

    def identity(self) -> str:
        """Stable string for cache keys; change it whenever output for the same input would change."""
        raise NotImplementedError

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None, variant: int = 0) -> bytes:
        """Return a WAV (any rate/channels; the renderer resamples). variant > 0 asks for a different
        take of the same line (a QA retry); deterministic backends should vary their sampling."""
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.manifest_path]

    def voices(self) -> VoiceLibrary:
        return VoiceLibrary(self.cfg.paths.voices_dir)

    def ensure_cast(self, wd: WorkDir, log=None) -> Cast:
        """cast.json is hand-editable: existing roles are kept; the narrator comes from config and
        speaking characters without a role are cast automatically from the voice library's tags."""
        cast = wd.read_cast() or Cast(roles={})
        narrator = self.cfg.cast.narrator
        if narrator and NARRATOR_ID not in cast.roles:
            cast.roles[NARRATOR_ID] = narrator
        if narrator and not cast.default_voice:
            cast.default_voice = narrator
        chars = wd.read_characters()
        if chars and self.NEEDS_VOICE_LIBRARY:
            spans = {ch.id: wd.read_spans(ch.id) for ch in wd.read_chapters().chapters if wd.spans_path(ch.id).exists()}
            new = auto_cast(cast, chars.characters, spans, self.voices().voices, self.cfg.cast.main_unique)
            if log and new:
                log(f"  cast {len(new)} new speaking characters:")
                for cid, vid, note in new[:20]:
                    log(f"    {cid:<28} -> {vid}" + (f"  [{note}]" if note else ""))
                if len(new) > 20:
                    log(f"    … and {len(new) - 20} more (see cast.json)")
        wd.write_cast(cast)
        return cast

    def run(self, ctx: Context) -> AudioManifest:
        from ...audio import require_ffmpeg

        require_ffmpeg()
        wd = ctx.need_wd()
        qa = None
        if self.settings.qa and self.NEEDS_VOICE_LIBRARY:  # (the dummy backend's beeps can't be transcribed)
            from ...qa import Transcriber, available

            if available():
                qa = Transcriber(self.settings.qa_model, self.settings.qa_threads)
            else:
                ctx.log("  QA skipped: faster-whisper not installed (uv sync --extra qa)")
        r = Renderer(self.cfg, wd, self, self.voices(), self.ensure_cast(wd, ctx.log),
                     strict_voices=self.NEEDS_VOICE_LIBRARY, qa=qa)
        m = r.render(only=ctx.chapters, progress=ctx.log)
        total = sum(c.duration_ms for c in m.chapters) / 1000
        ctx.log(f"  {len(m.chapters)} chapters, {total / 3600:.2f} h")
        return m
