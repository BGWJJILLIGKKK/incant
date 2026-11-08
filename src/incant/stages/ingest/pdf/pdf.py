"""PDF with a text layer (typeset, or scanned with OCR). Pure-Python pdfminer.six (MIT).

Works from text *lines and their geometry* rather than pdfminer's paragraph boxes (which scramble OCR'd
scans):
  1. per page, collect line fragments with position and font size; merge fragments sharing a baseline
     into visual lines; detect two-column pages and read the left column first
  2. drop running headers/footers (margin lines that recur on 3+ pages once digits are masked) and bare
     page numbers; drop footnotes (small type at the foot of the page)
  3. headings: lines set noticeably larger than the body text
  4. paragraphs from first-line indents, vertical gaps and short last lines; paragraphs continue across
     page breaks
  5. words hyphenated across lines are rejoined unless the hyphenated form is what the book uses
Scanned PDFs without a text layer are rejected with a clear message (OCR is not done here).
"""

from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ....normalize import clean_paragraph, clean_unicode, heading, is_scene_break, SCENE_BREAK
from ..base import Ingested, IngestMethod

__all__ = ["PdfIngest"]

MARGIN_BAND = 0.12  # top/bottom fraction of the page where running headers/footers live (scans: ~8-11%)
REPEAT_MIN = 3  # a margin line recurring on this many pages is a running header/footer (2 for short PDFs)
HEADING_RATIO = 1.22  # font size vs body text
FOOTNOTE_RATIO = 0.86
INDENT_EM = 0.8  # a line starting this far right of the column margin begins a paragraph
SHORT_LINE_EM = 3.0  # a sentence-final line ending this far short of the right margin ends one
_PAGE_NUM = re.compile(r"^[\[(]?(\d{1,4}|[ivxlcdm]{1,7})[\])]?\.?$", re.I)
_SENT_END = re.compile(r"[.!?…:;][”’\"')\]]*$")


@dataclass
class Line:
    x0: float
    x1: float
    top: float  # distance from the page top (pdf y is bottom-up)
    bottom: float
    size: float
    text: str
    col: int = 0  # column index on its page (two-column layouts)

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class Page:
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)


# ------------------------------------------------------------------ extraction


def _walk(obj, out: list):
    from pdfminer.layout import LTTextLineHorizontal

    if isinstance(obj, LTTextLineHorizontal):
        out.append(obj)
        return
    for child in getattr(obj, "_objs", []) or []:
        _walk(child, out)


def _line_size(line) -> float:
    from pdfminer.layout import LTChar

    sizes = [c.size for c in line if isinstance(c, LTChar) and c.get_text().strip()]
    return statistics.median(sizes) if sizes else line.height


def read_pages(path: Path) -> list[Page]:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams

    pages = []
    # line_margin small: keep pdfminer from gluing lines into blocks; we rebuild structure ourselves
    for lt in extract_pages(str(path), laparams=LAParams(line_margin=0.2, char_margin=2.0, word_margin=0.1)):
        raw: list = []
        _walk(lt, raw)
        frags = []
        for ln in raw:
            t = clean_unicode(ln.get_text()).replace("\xad", "-").strip()
            t = re.sub(r"^\d{1,3}\s+(?=[a-z])", "", t)  # stray margin number before a lowercase word
            if t:
                frags.append(Line(ln.x0, ln.x1, lt.height - ln.y1, lt.height - ln.y0, _line_size(ln), t))
        pages.append(Page(lt.width, lt.height, _visual_lines(frags, lt.width)))
    return pages


def _columns(frags: list[Line], width: float) -> list[list[Line]]:
    """Split into left/right columns when most lines sit clearly on one side of the middle."""
    mid = width / 2
    left = [f for f in frags if f.x1 < mid + width * 0.02]
    right = [f for f in frags if f.x0 > mid - width * 0.02]
    spanning = [f for f in frags if f not in left and f not in right]
    body = [f for f in frags if f.x1 - f.x0 > width * 0.15]
    n = len(frags)
    # Both sides populated (a column may end early on a chapter's last page) and almost nothing crossing.
    if body and min(len(left), len(right)) >= max(4, 0.1 * n) and len(spanning) <= 0.1 * n:
        return [left + spanning, right]
    return [frags]


def _is_mark(f: Line) -> bool:
    return len(f.text) <= 2 and not any(ch.isalnum() for ch in f.text)


def _visual_lines(frags: list[Line], width: float) -> list[Line]:
    """Merge fragments that share a baseline (OCR splits lines into pieces); top-to-bottom per column.
    Lone punctuation fragments (OCR'd running quotes, specks) join a line only if they sit inside its
    vertical band; floating between lines they are dropped."""
    marks = [f for f in frags if _is_mark(f)]
    frags = [f for f in frags if not _is_mark(f)]
    out: list[Line] = []
    for ci, col in enumerate(_columns(frags, width)):
        col.sort(key=lambda f: (f.top, f.x0))
        rows: list[list[Line]] = []
        for f in col:
            centre = (f.top + f.bottom) / 2
            for row in rows:
                r = row[0]
                if abs((r.top + r.bottom) / 2 - centre) < 0.5 * max(1.0, min(r.height, f.height)):
                    row.append(f)
                    break
            else:
                rows.append([f])
        for m in marks:
            mc = (m.top + m.bottom) / 2
            for row in rows:
                if min(f.top for f in row) <= mc <= max(f.bottom for f in row) and \
                        min(f.x0 for f in row) - 20 <= m.x0 <= max(f.x1 for f in row) + 20:
                    row.append(m)
                    break
        for row in sorted(rows, key=lambda r: min(f.top for f in r)):
            row.sort(key=lambda f: f.x0)
            out.append(Line(min(f.x0 for f in row), max(f.x1 for f in row), min(f.top for f in row),
                            max(f.bottom for f in row), statistics.median([f.size for f in row]),
                            " ".join(f.text for f in row), ci))
    return out


# ------------------------------------------------------------------ cleanup


def _mask(text: str) -> str:
    """Page numbers vary between pages, header text doesn't. Only digits are masked: "Chapter I" and
    "Chapter II" must stay distinct so real chapter headings are never mistaken for running headers."""
    return re.sub(r"\d+", "#", text.casefold()).strip(" .")


def strip_furniture(pages: list[Page], body_size: float) -> tuple[list[Page], dict[str, int]]:
    """Remove running headers/footers, page numbers and footnotes. Returns (pages, counts removed)."""
    def in_band(p: Page, ln: Line) -> bool:
        return ln.top < p.height * MARGIN_BAND or ln.bottom > p.height * (1 - MARGIN_BAND)

    def size_class(ln: Line) -> int:
        return 0 if ln.size < body_size * 0.92 else 2 if ln.size >= body_size * HEADING_RATIO else 1

    # keyed by text AND size class: an 8pt running header "CHAPTER II" must not cancel the 18pt heading
    recurring = Counter((_mask(ln.text), size_class(ln)) for p in pages for ln in p.lines if in_band(p, ln))
    known = {_letters(t) for (t, _), n in recurring.items() if n >= max(3, REPEAT_MIN) and len(_letters(t)) >= 6}

    def ocr_variant(ln: Line) -> bool:
        """An OCR-garbled copy of a known running header ("A CHRISTINIAS CAROL")."""
        k = _letters(ln.text)
        return len(k) >= 6 and any(difflib.SequenceMatcher(None, k, h).ratio() >= 0.8 for h in known)
    repeat_min = REPEAT_MIN if len(pages) >= 12 else 2
    removed = Counter()
    for p in pages:
        keep = []
        last_body: Line | None = None
        for ln in p.lines:
            small = ln.size < body_size * HEADING_RATIO
            tiny = ln.size < body_size * 0.92 and len(ln.text) < 80
            # Margin band: recurring lines (any size — OCR measures capitals large), bare page numbers, and
            # short small-type lines. A big line that doesn't recur is a chapter heading and stays.
            if in_band(p, ln) and (recurring[(_mask(ln.text), size_class(ln))] >= repeat_min or ocr_variant(ln)
                                   or (small and (_PAGE_NUM.match(ln.text) or tiny))):
                removed["header/footer/page number"] += 1
                continue
            # Footnotes: small type in the lower page, set off from the text above by a visible gap.
            gap_above = ln.top - last_body.bottom if last_body else 0
            if (ln.size < body_size * FOOTNOTE_RATIO and ln.top > p.height * 0.6 and gap_above >= body_size
                    and len(ln.text) > 3):
                removed["footnote"] += 1
                continue
            if ln.size >= body_size * FOOTNOTE_RATIO:
                last_body = ln
            keep.append(ln)
        # Image pages: a caption or two and nothing else. Keep only what reads as a heading.
        if 0 < len(keep) <= 2 and all(not _heading_like(ln.text) for ln in keep) and sum(len(ln.text) for ln in keep) < 160:
            removed["caption on image page"] += len(keep)
            keep = []
        p.lines = keep
    return pages, dict(removed)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.casefold())


def _heading_like(text: str) -> bool:
    """Plausible heading text: mostly clean words, a real word, no quotation marks, not ending in ! or ?"""
    t = text.strip()
    tokens = t.split()
    clean = [w for w in tokens if re.fullmatch(r"[A-Za-z][A-Za-z'’.,:;-]*", w)]
    if not tokens or len(clean) < 0.7 * len(tokens) or not re.search(r"[A-Za-z]{3}", t):
        return False
    return not re.search(r"[“”\"]|[!?]$", t)


def body_size_of(pages: list[Page]) -> float:
    sizes = Counter()
    for p in pages:
        for ln in p.lines:
            sizes[round(ln.size, 1)] += len(ln.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


# ------------------------------------------------------------------ paragraphs


def _vocab(pages: list[Page]) -> Counter:
    v = Counter()
    for p in pages:
        for ln in p.lines:
            v.update(w.casefold() for w in re.findall(r"[A-Za-z][A-Za-z'’-]*[A-Za-z]", ln.text))
    return v


def _open_quote(text: str) -> bool:
    curly = text.count("“") - text.count("”")
    return curly > 0 or (curly == 0 and text.count('"') % 2 == 1)


def _join(prev: str, nxt: str, vocab: Counter) -> str:
    """Join two lines of one paragraph, undoing end-of-line hyphenation where the book does, and dropping
    old-style "running quotes" (a quote mark repeated at the start of each line of a quotation)."""
    if re.match(r'^["“]\s', nxt) and _open_quote(prev):
        nxt = nxt[1:].lstrip()
    m = re.search(r"([A-Za-z]+)-$", prev)
    if m and nxt[:1].islower():
        head, tail = m.group(1), re.match(r"[A-Za-z]+", nxt)
        tail_s = tail.group(0) if tail else ""
        joined, hyphenated = (head + tail_s).casefold(), f"{head}-{tail_s}".casefold()
        if vocab[hyphenated] > vocab[joined]:
            return prev + nxt  # the book writes it hyphenated ("well-known")
        return prev[:-1] + nxt
    return prev + " " + nxt


def _column_geometry(lines: list[Line], body_size: float, em: float) -> tuple[float, float, float, float]:
    """(left margin, right margin, normal line gap, extra gap that starts a paragraph) of one column. The left margin is the smallest left
    edge used by a fair share of lines — not the most common one, which on dialogue-heavy pages is the
    paragraph indent."""
    body = [ln for ln in lines if ln.size < body_size * HEADING_RATIO] or lines
    xs = Counter(round(ln.x0 / 2) * 2 for ln in body)
    common = [x for x, n in xs.items() if n >= max(2, 0.1 * len(body))]
    left = min(common) if common else min(ln.x0 for ln in body)
    rights = sorted(ln.x1 for ln in body)
    right = rights[int(0.9 * (len(rights) - 1))] if rights else 0
    gaps = [b.top - a.bottom for a, b in zip(lines, lines[1:]) if 0 < b.top - a.bottom < 3 * em]
    if not gaps:
        return left, right, em * 0.3, 0.35 * em
    med = statistics.median(gaps)
    mad = statistics.median(abs(g - med) for g in gaps)
    # paragraph gap: well above normal spacing; scans have uneven spacing, so scale with its spread
    return left, right, med, max(0.35 * em, 4 * mad)


def paragraphs(pages: list[Page], body_size: float) -> list[str]:
    vocab = _vocab(pages)
    out: list[str] = []
    cur: str | None = None
    prev: Line | None = None
    prev_right = 0.0
    em = body_size

    def flush():
        nonlocal cur
        if cur is not None and cur.strip():
            out.append(cur)
        cur = None

    for p in pages:
        if not p.lines:
            continue
        geo = {c: _column_geometry([ln for ln in p.lines if ln.col == c], body_size, em) for c in {ln.col for ln in p.lines}}
        for i, ln in enumerate(p.lines):
            left, right, gap, para_gap = geo[ln.col]
            starts_block = i == 0 or ln.top - p.lines[i - 1].bottom > 1.5 * em or (
                prev is not None and prev.size >= body_size * HEADING_RATIO)
            if ln.size >= body_size * HEADING_RATIO and len(ln.text) < 120 and not _heading_like(ln.text) \
                    and not re.search(r"[A-Za-z]{3}", ln.text):
                continue  # OCR noise from covers, stamps, ornaments
            if ln.size >= body_size * HEADING_RATIO and len(ln.text) < 120 and starts_block and _heading_like(ln.text):
                flush()
                if out and out[-1].startswith("# ") and prev is not None and prev.size >= body_size * HEADING_RATIO:
                    out[-1] += " " + ln.text  # a heading set on two lines
                else:
                    out.append(heading(ln.text))
                prev, prev_right = ln, right
                continue
            indented = ln.x0 > left + INDENT_EM * em
            same_page_prev = i > 0 and prev is p.lines[i - 1] and prev.col == ln.col
            big_gap = same_page_prev and ln.top - prev.bottom > gap + para_gap
            # A short last line ending a sentence closes the paragraph — also across column/page breaks,
            # where gaps and indents tell nothing (measured against the previous line's own column).
            prev_short = prev is not None and prev.x1 < prev_right - SHORT_LINE_EM * em and bool(_SENT_END.search(prev.text))
            new_para = cur is None or indented or big_gap or prev_short
            if is_scene_break(ln.text):
                flush()
                out.append(SCENE_BREAK)
            elif new_para:
                flush()
                cur = ln.text
            else:
                cur = _join(cur, ln.text, vocab)
            prev, prev_right = ln, right
    flush()
    return [p if p.startswith("# ") or p == SCENE_BREAK else clean_paragraph(p) for p in out]


# ------------------------------------------------------------------ method


def _info(path: Path) -> dict[str, str]:
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser

    out = {}
    with open(path, "rb") as f:
        doc = PDFDocument(PDFParser(f))
        for info in doc.info or []:
            for k in ("Title", "Author"):
                v = info.get(k)
                if isinstance(v, bytes):
                    v = v.decode("utf-16" if v.startswith(b"\xfe\xff") else "latin-1", errors="ignore")
                if v and str(v).strip():
                    out[k.lower()] = str(v).strip()
    return out


def ingest_pdf(path: Path) -> Ingested:
    pages = read_pages(path)
    chars = sum(len(ln.text) for p in pages for ln in p.lines)
    if chars < 200:
        raise RuntimeError(f"{path.name}: no usable text layer ({chars} characters over {len(pages)} pages) — "
                           "a scanned PDF needs OCR first (e.g. `ocrmypdf in.pdf out.pdf`)")
    body = body_size_of(pages)
    pages, removed = strip_furniture(pages, body)
    paras = paragraphs(pages, body)
    info = _info(path)
    warnings = [f"removed {n} {what} lines" for what, n in removed.items()]
    empty = sum(1 for p in pages if not p.lines)
    if empty:
        warnings.append(f"{empty} of {len(pages)} pages have no text (images or blank)")
    return Ingested(title=info.get("title") or path.stem.replace("_", " ").replace("-", " ").title(),
                    author=info.get("author"), paragraphs=paras, source_format="pdf", warnings=warnings)


class PdfIngest(IngestMethod):
    """PDF with a text layer: running headers/footers, page numbers and footnotes removed; columns,
    paragraphs, headings and end-of-line hyphenation reconstructed."""
    LICENSE = 'project code; pdfminer.six (MIT)'
    OUTPUT_TERMS = 'none'

    @classmethod
    def accepts(cls, source: str) -> bool:
        p = Path(source)
        return p.is_file() and p.suffix.lower() == ".pdf"

    def load(self, source: str) -> Ingested:
        return ingest_pdf(Path(source))
