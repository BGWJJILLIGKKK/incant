"""Context rules for heteronyms Misaki can't separate by part of speech alone.

bow: "bowed to the king", "a deep bow", "the bows of the ship" (rhymes with now) versus "bowed the cello",
"a bow in her hair", "bow and arrow" (rhymes with go). Misaki always says the second. Verbs get "now" unless
strings or an instrument follow; nouns get "now" next to a gesture or a ship, "go" next to ribbons, weapons
or instruments (the default for a noun with no such cue).

Misaki's phoneme shorthand: O = the vowel of "go", W = the vowel of "now".
"""

from __future__ import annotations

import re

BOW = re.compile(r"^bow(s|ed|ing)?$", re.I)
STRINGS = {"violin", "viola", "cello", "bass", "fiddle", "string", "strings", "instrument", "note", "notes", "chord"}
GO_NOUN = STRINGS | {"arrow", "arrows", "ribbon", "ribbons", "tie", "tied", "tying", "hair", "archer", "archers",
                     "quiver", "drew", "draw", "draws", "shoot", "shot", "longbow", "crossbow", "silk", "satin",
                     "lace", "bonnet", "package", "present", "gift", "neck", "rainbow"}
NOW_NOUN = {"low", "lower", "lowest", "deep", "deeper", "slight", "polite", "courtly", "stiff", "little", "gracious",
            "formal", "curt", "made", "make", "makes", "making", "gave", "give", "with", "sweeping", "profound",
            "final", "elaborate", "respectful", "mock", "ironic", "solemn", "graceful", "ship", "ships", "boat",
            "boats", "stern", "deck", "vessel", "sea", "waves", "port", "starboard", "hull", "mast"}


def _words(tokens, i: int, before: int, after: int) -> set[str]:
    lo, hi = max(0, i - before), min(len(tokens), i + after + 1)
    return {t.text.casefold() for j, t in enumerate(tokens[lo:hi], lo) if j != i}


def fix(tokens) -> int:
    """Adjust Misaki tokens in place; returns how many were changed."""
    changed = 0
    for i, t in enumerate(tokens):
        if not t.phonemes or not BOW.match(t.text) or "O" not in t.phonemes:
            continue
        tag = t.tag or ""
        if tag.startswith("VB"):
            now = not (_words(tokens, i, 0, 3) & STRINGS)
        else:
            near = _words(tokens, i, 3, 3)
            now = bool(near & NOW_NOUN) and not (near & GO_NOUN)
        if now:
            t.phonemes = t.phonemes.replace("O", "W", 1)
            changed += 1
    return changed


def phonemes(tokens) -> str:
    return "".join((t.phonemes or "") + t.whitespace for t in tokens)
