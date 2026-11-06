"""Stage 4 — synthesize: spans + cast -> audio/chapters/<id>.wav + audio/manifest.json.

A method is a TTS backend: implement `identity()` and `synthesize(text, voice, emotion) -> wav bytes`.
The shared renderer (render.py) does casting, chunking, caching, pauses and loudness normalization.
"""

from .base import SynthesizeMethod as Base
from .base import SynthSettings

ORDER = 4
DESCRIPTION = "text-to-speech per segment; assemble normalized chapter audio"

__all__ = ["Base", "SynthSettings", "ORDER", "DESCRIPTION"]
