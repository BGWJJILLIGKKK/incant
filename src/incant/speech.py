"""Rule-based English text normalisation for TTS: spell out what a TTS engine is likely to misread.

Used by the `normalize` stage (normalize/rules) and by QA, which runs the same rules over whisper's
transcript so "Mr." heard vs "Mister" written isn't a mismatch.

Order matters; see `normalize()`. Every rule is conservative: when unsure, leave the text alone.
"""

from __future__ import annotations

import re

from num2words import num2words

from .textprep import speakable

# ------------------------------------------------------------------ helpers


def _card(n: int) -> str:
    return num2words(n).replace(",", "")  # "one thousand, five hundred" would add a pause


def _ord(n: int) -> str:
    return num2words(n, to="ordinal").replace(",", "")


def _year(n: int) -> str:
    return num2words(n, to="year")


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_RE = r"(?:M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
_ROMAN_FULL = re.compile(rf"^{_ROMAN_RE}$")


def roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or not _ROMAN_FULL.match(s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_VALUES[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


# ------------------------------------------------------------------ rules

# Abbreviations that precede a name: expanded with no period kept.
_TITLES = {
    "Mr": "Mister", "Mrs": "Missus", "Ms": "Miz", "Dr": "Doctor", "Messrs": "Messieurs", "Mme": "Madame",
    "Mlle": "Mademoiselle", "Capt": "Captain", "Col": "Colonel", "Gen": "General", "Lt": "Lieutenant",
    "Sgt": "Sergeant", "Rev": "Reverend", "Prof": "Professor", "Hon": "Honourable", "Gov": "Governor",
    "Sen": "Senator", "Rep": "Representative", "Mt": "Mount", "Ft": "Fort", "Pres": "President",
}
# Abbreviations that follow something or stand alone: a sentence-ending period is kept.
_TRAILING = {
    "Jr": "Junior", "Sr": "Senior", "Esq": "Esquire", "etc": "et cetera", "vs": "versus", "Co": "Company",
    "Bros": "Brothers", "Inc": "Incorporated", "Ltd": "Limited", "approx": "approximately",
}
_LATIN = {"e.g.": "for example", "i.e.": "that is", "viz.": "namely", "cf.": "compare", "a.m.": "A M", "p.m.": "P M",
          "A.M.": "A M", "P.M.": "P M"}
# Capitalised section words followed by a Roman numeral read as a cardinal: "Chapter IV" -> "Chapter four".
_SECTION_WORDS = ["Chapter", "Book", "Part", "Volume", "Vol.", "Act", "Scene", "Letter", "Canto", "Section", "Stave",
                  "Psalm", "Article", "Appendix", "Phase"]
_SECTION = "(?:" + "|".join(re.escape(w) for w in _SECTION_WORDS + [w.upper() for w in _SECTION_WORDS]) + ")"
# Regnal names: "Henry VIII" -> "Henry the Eighth" (also allows I).
_REGNAL = {"Henry", "Edward", "George", "William", "Charles", "James", "Richard", "Louis", "Elizabeth", "Mary",
           "Anne", "John", "Philip", "Frederick", "Peter", "Catherine", "Pius", "Leo", "Gregory", "Innocent",
           "Benedict", "Clement", "Urban", "Boniface", "Alexander", "Nicholas", "Ivan", "Napoleon", "Victor",
           "Ferdinand", "Isabella", "Alfonso", "Gustavus", "Christian", "Olaf", "Harald", "Wilhelm", "Friedrich",
           "Otto", "Rudolph", "Ludwig", "Carl", "Karl", "Francis", "Joseph", "Paul", "Stephen", "Robert", "David",
           "Alexius", "Constantine", "Basil", "Ptolemy", "Rameses", "Ramesses", "Antiochus", "Cleopatra"}
_ACRONYMS = {"OK", "TV", "UK", "US", "USA", "BBC", "FBI", "CIA", "NASA", "NATO", "UN", "EU", "DNA", "CEO", "PhD",
             "AI", "ID", "PC", "DJ", "MP", "RAF", "HMS", "USS", "GPS", "CD", "DVD", "VIP", "IQ", "UFO", "SOS"}
_ARCHAIC = {"to-day": "today", "to-morrow": "tomorrow", "to-night": "tonight", "to-morrow's": "tomorrow's",
            "to-day's": "today's", "to-night's": "tonight's", "any-one": "anyone",
            "every-one": "everyone", "some-one": "someone", "no-one": "no one"}
_FRACTIONS = {"½": "one half", "⅓": "one third", "⅔": "two thirds", "¼": "one quarter", "¾": "three quarters",
              "⅛": "one eighth", "⅜": "three eighths", "⅝": "five eighths", "⅞": "seven eighths", "⅕": "one fifth"}
_SYMBOLS = {"&c.": "etc.", "&c": "etc", "&": " and ", "§": " section ", "°": " degrees ", "×": " by ",
            "+": " plus ", "=": " equals ", "@": " at ", "~": " about "}


def _keep_period(rest: str) -> str:
    """After an expanded abbreviation: keep '.' if it was also the end of the sentence."""
    return "." if not rest.strip() or re.match(r"\s+[“\"‘(]?[A-Z]", rest) else ""


def _abbreviations(t: str) -> str:
    for k, v in _LATIN.items():
        t = re.sub(rf"(?<!\w){re.escape(k)}", v, t)
    # St. before a capitalised word is Saint; after one it is Street.
    t = re.sub(r"\bSt\.(?=\s+[A-Z])", "Saint", t)
    t = re.sub(r"(?<=[A-Za-z]\s)St\.", "Street", t)
    t = re.sub(r"\b(" + "|".join(_TITLES) + r")\.(?=\s|$)", lambda m: _TITLES[m.group(1)], t)
    t = re.sub(r"\b(Mr|Mrs|Ms|Dr)(?=\s+[A-Z])", lambda m: _TITLES[m.group(1)], t)  # British "Mr Darcy"

    def trailing(m: re.Match) -> str:
        return _TRAILING[m.group(1)] + _keep_period(m.string[m.end():])

    t = re.sub(r"\b(" + "|".join(_TRAILING) + r")\.", trailing, t)
    t = re.sub(r"\b[Nn]o\.\s*(?=\d)", "number ", t)
    return t


def _money(t: str) -> str:
    def pounds(m: re.Match) -> str:
        n = int(m.group(1).replace(",", ""))
        out = f"{_card(n)} pound{'s' if n != 1 else ''}"
        if m.group(2):
            out += f" {_card(int(m.group(2)))}"
        return out

    def dollars(m: re.Match) -> str:
        n = int(m.group(1).replace(",", ""))
        out = f"{_card(n)} dollar{'s' if n != 1 else ''}"
        if m.group(2) and int(m.group(2)):
            c = int(m.group(2))
            out += f" and {_card(c)} cent{'s' if c != 1 else ''}"
        return out

    t = re.sub(r"£\s?(\d[\d,]*)(?:\.(\d\d))?", pounds, t)
    t = re.sub(r"\$\s?(\d[\d,]*)(?:\.(\d\d))?", dollars, t)
    # pre-decimal British money: "2s. 6d.", "6d.", "5s."
    t = re.sub(r"\b(\d{1,2})s\.\s?(\d{1,2})d\.", lambda m: f"{_card(int(m.group(1)))} shillings and {_card(int(m.group(2)))} pence", t)
    t = re.sub(r"\b(\d{1,2})d\.", lambda m: f"{_card(int(m.group(1)))} pence", t)
    t = re.sub(r"\b(\d{1,2})s\.(?=\s|$)", lambda m: f"{_card(int(m.group(1)))} shillings", t)
    return t


def _pronoun_like(num: str, rest: str) -> bool:
    """A lone "I" followed by a word is the pronoun ("Mary I think", "Part I was"), not a numeral."""
    return num == "I" and re.match(r"\s*[A-Za-z]", rest) is not None


def _roman(t: str, title: bool, whole_span: bool) -> str:
    def section(m: re.Match) -> str:
        num = m.group(2)
        n = roman_to_int(num)
        if not n or _pronoun_like(num, m.string[m.end():]):
            return m.group(0)
        return f"{m.group(1)} {_card(n)}"

    t = re.sub(rf"(?<!\w)({_SECTION})\s+({_ROMAN_RE})\b(?!['’]\w)", lambda m: section(m) if m.group(2) else m.group(0), t)

    def regnal(m: re.Match) -> str:
        name, num = m.group(1), m.group(2)
        n = roman_to_int(num)
        if not n or _pronoun_like(num, m.string[m.end():]) or (len(num) == 1 and name not in _REGNAL):
            return m.group(0)
        return f"{name} the {_ord(n).capitalize()}"

    t = re.sub(rf"\b([A-Z][a-z]+)\s+({_ROMAN_RE})\b(?![a-z'’])", lambda m: regnal(m) if m.group(2) else m.group(0), t)
    if title or whole_span:  # "I. A SCANDAL IN BOHEMIA" / a span that is just "IV."
        def lead(m: re.Match) -> str:
            n = roman_to_int(m.group(1))
            return f"{_card(n).capitalize()}{m.group(2)}" if n else m.group(0)

        pattern = rf"^\s*({_ROMAN_RE})(\.|:)(?=\s|$)" if title else rf"^\s*({_ROMAN_RE})(\.?)\s*$"
        t = re.sub(pattern, lambda m: lead(m) if m.group(1) else m.group(0), t)
    return t


def _numbers(t: str) -> str:
    for k, v in _FRACTIONS.items():
        t = re.sub(rf"(\d)\s*{k}", rf"\1 and {v}", t).replace(k, v)
    t = re.sub(r"\b1/2\b", "one half", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: _card(int(m.group(1))) + (
        " o'clock" if m.group(2) == "00" else " oh " + _card(int(m.group(2))) if m.group(2).startswith("0")
        else " " + _card(int(m.group(2)))), t)
    t = re.sub(r"\b(\d[\d,]*)\s?%", lambda m: f"{_card(int(m.group(1).replace(',', '')))} percent", t)
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: _ord(int(m.group(1))), t)
    t = re.sub(r"\b(\d+)\.(\d+)\b", lambda m: f"{_card(int(m.group(1)))} point {' '.join(_card(int(d)) for d in m.group(2))}", t)
    # year ranges "1810-1820" / "1810–20"
    t = re.sub(r"\b(1[1-9]\d\d|20\d\d)\s?[-–]\s?(\d{2,4})\b",
               lambda m: f"{_year(int(m.group(1)))} to {_year(int(m.group(2)) if len(m.group(2)) == 4 else int(m.group(1)[:2] + m.group(2)))}", t)
    t = re.sub(r"\b(1[1-9]\d\d|20\d\d)s\b", lambda m: _year(int(m.group(1))).replace("ty", "ties") if m.group(1).endswith("0") else m.group(0), t)
    t = re.sub(r"(?<![\d,.])\b(1[1-9]\d\d|20\d\d)\b(?!\d|,\d)", lambda m: _year(int(m.group(1))), t)
    t = re.sub(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b", lambda m: _card(int(m.group(0).replace(",", ""))), t)
    return t


def _caps(t: str) -> str:
    """ALL-CAPS words a TTS may spell letter by letter: runs of 2+ caps words, single words of 5+ letters,
    and a leading drop-cap word ("IT is a truth") become Title case. Known acronyms are kept."""
    tok = re.compile(r"\b[A-Z][A-Z'’]+\b")

    def fix(w: str) -> str:
        return w if w in _ACRONYMS else w[0] + w[1:].lower()

    spans = [m for m in tok.finditer(t)]
    out, last = [], 0
    for i, m in enumerate(spans):
        w = m.group(0)
        prev_caps = i > 0 and re.fullmatch(r"[\s.,;:—\-]*", t[spans[i - 1].end():m.start()]) is not None
        next_caps = i + 1 < len(spans) and re.fullmatch(r"[\s.,;:—\-]*", t[m.end():spans[i + 1].start()]) is not None
        leading = m.start() == len(t) - len(t.lstrip()) and re.match(r"\s+[a-z]", t[m.end():])
        if w not in _ACRONYMS and (prev_caps or next_caps or len(w) >= 5 or leading):
            out.append(t[last:m.start()] + fix(w))
            last = m.end()
    out.append(t[last:])
    return "".join(out)


def _symbols(t: str) -> str:
    for k, v in _SYMBOLS.items():
        t = t.replace(k, v)
    t = re.sub(r"#\s?(?=\d)", "number ", t)
    t = re.sub(r"[—–]{2,}", "—", t)  # Austen's "——"
    t = re.sub(r"(?<=\d)\s?[–](?=\d)", " to ", t)  # en-dash ranges
    return t


def _archaic(t: str) -> str:
    for k, v in _ARCHAIC.items():
        t = re.sub(rf"\b{re.escape(k)}", lambda m, v=v: v.capitalize() if m.group(0)[0].isupper() else v, t, flags=re.I)
    return t


def normalize(text: str, *, title: bool = False, whole_span: bool = False) -> str:
    """TTS-ready text. `title`: a chapter title ("I. A SCANDAL IN BOHEMIA"); `whole_span`: the text is a
    complete narration span, so a lone Roman numeral ("IV.") is a section number."""
    t = speakable(text)
    if not t:
        return ""
    t = _symbols(t)
    t = _money(t)
    t = _roman(t, title, whole_span)
    t = _caps(t)  # after Roman numerals (CHAPTER III), before abbreviations (MR. -> Mr.)
    t = _abbreviations(t)
    t = _archaic(t)
    t = _numbers(t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()
