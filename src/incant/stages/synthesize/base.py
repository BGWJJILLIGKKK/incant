from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ...schemas import NARRATOR_ID, AudioManifest, Cast, Emotion, Voice
from ...voices import VoiceLibrary
from ...workdir import WorkDir
from ..base import Context, Method
from .render import Renderer


class SynthSettings(BaseModel):
    concurrency: int = 1
    max_segment_chars: int = 300


class SynthesizeMethod(Method):
    Settings: ClassVar[type[BaseModel]] = SynthSettings
    settings: SynthSettings

    # False for backends that don't need reference clips (voice ids are just labels).
    NEEDS_VOICE_LIBRARY: ClassVar[bool] = True

    def identity(self) -> str:
        """Stable string for cache keys; change it whenever output for the same input would change."""
        raise NotImplementedError

    def synthesize(self, text: str, voice: Voice | None, emotion: Emotion | None) -> bytes:
        """Return a WAV (any rate/channels; the renderer resamples)."""
        raise NotImplementedError

    @classmethod
    def outputs(cls, wd: WorkDir) -> list[Path]:
        return [wd.manifest_path]

    def voices(self) -> VoiceLibrary:
        return VoiceLibrary(self.cfg.paths.voices_dir)

    def ensure_cast(self, wd: WorkDir) -> Cast:
        """cast.json is hand-editable: keep it, only filling the narrator from config when absent."""
        cast = wd.read_cast() or Cast(roles={})
        narrator = self.cfg.cast.narrator
        if narrator and NARRATOR_ID not in cast.roles:
            cast.roles[NARRATOR_ID] = narrator
        if narrator and not cast.default_voice:
            cast.default_voice = narrator
        wd.write_cast(cast)
        return cast

    def run(self, ctx: Context) -> AudioManifest:
        from ...audio import require_ffmpeg

        require_ffmpeg()
        wd = ctx.need_wd()
        r = Renderer(self.cfg, wd, self, self.voices(), self.ensure_cast(wd), strict_voices=self.NEEDS_VOICE_LIBRARY)
        m = r.render(only=ctx.chapters, progress=ctx.log)
        total = sum(c.duration_ms for c in m.chapters) / 1000
        ctx.log(f"  {len(m.chapters)} chapters, {total / 3600:.2f} h")
        return m
