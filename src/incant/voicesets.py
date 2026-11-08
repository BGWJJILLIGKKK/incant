"""Built-in voice lists for backends whose voices are fixed by the model (no reference clips).

Tags use the casting vocabulary (gender: female/male, age: teen/adult/older/neutral, tone: high/neutral/deep).
Only voices with a gender tag are cast automatically; the rest can still be chosen by hand in cast.json.
Override or extend any of this in voices/<backend>.json, e.g. {"af_sky": {"age": "teen"}}.
"""

from __future__ import annotations

from .schemas import Voice

# Kokoro-82M v1.0. Id = <language><gender>_<name>; the first letter picks the accent/language.
KOKORO_LANG = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja",
               "p": "pt-br", "z": "cmn"}
# Grades from the model card (A best … F); D-and-better English voices (except the
# whispery af_nicole) are cast automatically.
_KOKORO_EN = {
    "af_heart": ("A", None, None), "af_bella": ("A-", None, None), "af_nicole": ("B-", None, "whisper"),
    "af_aoede": ("C+", None, None), "af_kore": ("C+", None, None), "af_sarah": ("C+", None, None),
    "af_nova": ("C", None, None), "af_alloy": ("C", None, None), "af_sky": ("C-", "teen", "high"),
    "af_jessica": ("D", None, None), "af_river": ("D", None, None),
    "am_fenrir": ("C+", None, "deep"), "am_michael": ("C+", None, None), "am_puck": ("C+", "teen", "high"),
    "am_echo": ("D", None, None), "am_eric": ("D", None, None), "am_liam": ("D", None, None),
    "am_onyx": ("D", None, "deep"), "am_santa": ("D", "older", "deep"), "am_adam": ("F+", None, None),
    "bf_emma": ("B-", None, None), "bf_isabella": ("C", None, None), "bf_alice": ("D", None, None),
    "bf_lily": ("D", None, None),
    "bm_fable": ("C", None, None), "bm_george": ("C", "older", "deep"), "bm_daniel": ("D", None, None),
    "bm_lewis": ("D+", None, "deep"),
}
_KOKORO_OTHER = ["ef_dora", "em_alex", "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
                 "if_sara", "im_nicola", "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
                 "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
                 "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang"]


def kokoro_voice(vid: str) -> Voice:
    lang = KOKORO_LANG.get(vid[:1])
    gender = {"f": "female", "m": "male"}.get(vid[1:2])
    if vid in _KOKORO_EN:
        grade, age, tone = _KOKORO_EN[vid]
        castable = grade[0] in "ABCD" and tone != "whisper"
        return Voice(id=vid, name=vid.split("_", 1)[1].title(), lang=lang, gender=gender if castable else None,
                     age=age or "adult", tone=tone if tone in ("high", "deep") else "neutral",
                     description=f"Kokoro {lang} {gender} voice, grade {grade}" + (", whispery" if tone == "whisper" else ""),
                     source="kokoro-82m", license="Apache-2.0")
    return Voice(id=vid, name=vid.split("_", 1)[1].title(), lang=lang,
                 description=f"Kokoro {lang} {gender} voice (not cast automatically for English text)",
                 source="kokoro-82m", license="Apache-2.0")


KOKORO = {v: kokoro_voice(v) for v in [*_KOKORO_EN, *_KOKORO_OTHER]}
KOKORO_NARRATOR = "af_heart"

# OpenAI /v1/audio/speech (tts-1, gpt-4o-mini-tts). Genders as commonly perceived; adjust in voices/openai.json.
_OPENAI = {"alloy": "female", "ash": "male", "ballad": "male", "coral": "female", "echo": "male", "fable": "male",
           "nova": "female", "onyx": "male", "sage": "female", "shimmer": "female", "verse": "male"}
OPENAI = {v: Voice(id=v, name=v.title(), gender=g, age="adult", tone="deep" if v == "onyx" else "neutral",
                   source="openai") for v, g in _OPENAI.items()}

BUILTIN = {"kokoro": (KOKORO, KOKORO_NARRATOR), "openai": (OPENAI, "nova"), "none": ({}, None)}
