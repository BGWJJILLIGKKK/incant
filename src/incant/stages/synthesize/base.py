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
    # Narrator voice for this backend; else [cast] narrator if this backend has that voice; else the backend's own default.
    narrator: str | None = None
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

    # False for backends whose voice ids are just labels (dummy): any id is accepted, nothing is cast.
    NEEDS_VOICE_LIBRARY: ClassVar[bool] = True
    # False for backends whose output can't be checked with speech-to-text (dummy beeps).
    QA_SUPPORTED: ClassVar[bool] = True
    # Narrator when neither the method's `narrator` setting nor [cast] narrator names one of its voices.
    DEFAULT_NARRATOR: ClassVar[str | None] = None

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
        """This backend's voices. Default: voices/voices.json (+ voices/<id>.json), the reference-clip library."""
        return VoiceLibrary(self.cfg.paths.voices_dir)

    def voice_space(self) -> str | None:
        """Name of the set of voice ids this backend understands. Each space keeps its own cast, so switching
        backends doesn't feed one backend's voice ids to another. None: ids are labels (dummy)."""
        return self.name if self.NEEDS_VOICE_LIBRARY else None

    def narrator(self) -> str | None:
        lib = self.voices().voices
        for v in (self.settings.narrator, self.cfg.cast.narrator, self.DEFAULT_NARRATOR):
            if v and (v in lib or not self.NEEDS_VOICE_LIBRARY):
                return v
        return None

    def load_cast(self, wd: WorkDir, log=None) -> Cast:
        """The book's cast for this backend's voice space. cast.json is the active one; switching backends
        puts it aside as cast.<space>.json and brings back the new backend's own (hand edits survive)."""
        space = self.voice_space()
        cast = wd.read_cast()
        if space is None or cast is None or cast.engine == space:
            return cast or Cast(roles={}, engine=space)
        lib = self.voices().voices
        ids = [*cast.roles.values(), *([cast.default_voice] if cast.default_voice else [])]
        if cast.engine is None and all(v in lib for v in ids):
            cast.engine = space  # an older cast.json, written for this backend
            return cast
        old = cast.engine or "unknown"
        wd.write_cast(cast, wd.cast_path_for(old))
        saved = wd.read_cast(wd.cast_path_for(space))
        if log:
            log(f"  voices: {old} -> {space}; {old}'s cast kept in {wd.cast_path_for(old).name}"
                + (f", {space}'s restored" if saved else ""))
        return saved or Cast(roles={}, engine=space)

    def ensure_cast(self, wd: WorkDir, log=None) -> Cast:
        """cast.json is hand-editable: existing roles are kept; the narrator comes from config and
        speaking characters without a role are cast automatically from the voice library's tags."""
        cast = self.load_cast(wd, log)
        narrator = self.narrator()
        if narrator and NARRATOR_ID not in cast.roles:
            cast.roles[NARRATOR_ID] = narrator
        if narrator and not cast.default_voice:
            cast.default_voice = narrator
        chars = wd.read_characters()
        if chars and self.NEEDS_VOICE_LIBRARY:
            spans = {ch.id: wd.read_spans(ch.id) for ch in wd.read_chapters().chapters if wd.spans_path(ch.id).exists()}
            new = auto_cast(cast, chars.characters, spans, self.voices().voices, self.cfg.cast.main_unique)
            nc = chars.narrator
            if nc and nc != NARRATOR_ID and self.cfg.cast.first_person_narration == "character" and nc in cast.roles:
                if cast.roles.get(NARRATOR_ID) != cast.roles[nc] and log:
                    log(f"  first-person book: narration uses {nc}'s voice ({cast.roles[nc]})")
                cast.roles[NARRATOR_ID] = cast.roles[nc]
            if log and new:
                log(f"  cast {len(new)} speaking characters automatically (hand-edited roles kept):")
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
        if self.settings.qa and self.QA_SUPPORTED:
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

    def check(self) -> list[tuple[bool, str]]:
        lib = self.voices()
        out = [(bool(lib.voices), f"{len(lib.voices)} voices ({lib.describe()})")]
        narr = self.narrator()
        out.append((narr is not None, f"narrator voice: {narr!r}" if narr else
                    f"no narrator voice: set narrator under [synthesize.{self.name}] or [cast]"))
        return out
