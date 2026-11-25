"""PDF ingest against typeset PDFs with known content (tests/pdf_fixtures.py)."""

import random

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pyphen")

from pdf_fixtures import FOOTNOTE, make_pdf  # noqa: E402

from incant.stages.ingest.pdf.pdf import PdfIngest, ingest_pdf  # noqa: E402

WORDS = ("the morning was extraordinarily quiet and notwithstanding every acknowledgement of the "
         "neighbourhood the well-known gentleman walked slowly towards the house where his correspondence "
         "waited unopened beside the window").split()


def book(chapters=3, paras=14, seed=7):
    rnd = random.Random(seed)
    out = []
    for c in range(chapters):
        ps = []
        for p in range(paras):
            sentences = []
            for _ in range(rnd.randint(1, 5)):
                ws = [rnd.choice(WORDS) for _ in range(rnd.randint(6, 22))]
                sentences.append(" ".join(ws).capitalize() + rnd.choice([".", ".", "!", "?"]))
            text = " ".join(sentences)
            if p % 4 == 1:
                text = f"“{text}” said Mr. Brown, “and well-known to all.”"
            ps.append(text)
        out.append((f"Chapter {['I', 'II', 'III', 'IV'][c]}", ps))
    return out


@pytest.mark.parametrize("layout", ["book", "columns"])
def test_pdf_reconstructs_text(tmp_path, layout):
    chapters = book()
    f = tmp_path / f"{layout}.pdf"
    make_pdf(f, chapters, layout, title="Test Book", author="A. Writer")
    ing = ingest_pdf(f)
    heads = [p for p in ing.paragraphs if p.startswith("# ")]
    paras = [p for p in ing.paragraphs if not p.startswith("# ")]
    want = [p for _, ps in chapters for p in ps]
    assert heads == ["# Chapter I", "# Chapter II", "# Chapter III"]
    assert paras == want  # every paragraph exact: no headers, page numbers, footnotes; hyphenation undone
    assert ing.title == "Test Book" and ing.author == "A. Writer"
    assert not any(FOOTNOTE[:20] in p or "TEST BOOK" in p for p in ing.paragraphs)


def test_pdf_hyphenation_was_exercised(tmp_path):
    """Guard against the test passing vacuously: the PDF really has end-of-line hyphens."""
    from incant.stages.ingest.pdf.pdf import read_pages

    f = tmp_path / "b.pdf"
    make_pdf(f, book(), "book")
    ends = [ln.text for p in read_pages(f) for ln in p.lines if ln.text.endswith("-")]
    assert len(ends) >= 3


def test_pdf_gutenberg_boilerplate_with_inline_and_wrapped_markers(tmp_path):
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "gutenberg.pdf"
    body = "Alice opened the door. Beyond the garden she could hear the river running over the stones."
    pdf = Canvas(str(path))
    pdf.drawString(40, 750, "License introduction. ***START OF THE PROJECT")
    pdf.drawString(40, 736, "GUTENBERG EBOOK TEST BOOK*** " + body)
    pdf.drawString(40, 700, "She followed the path. *** END OF THE PROJECT")
    pdf.drawString(40, 686, "GUTENBERG EBOOK TEST BOOK *** License footer.")
    pdf.save()
    result = ingest_pdf(path)
    assert result.paragraphs == [body, "She followed the path."]
    assert "removed Project Gutenberg boilerplate" in result.warnings


def test_pdf_without_text_layer_is_rejected(tmp_path):
    from reportlab.pdfgen import canvas

    f = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(f))
    c.rect(50, 50, 400, 600, fill=1)
    c.showPage()
    c.save()
    with pytest.raises(RuntimeError, match="no usable text layer"):
        ingest_pdf(f)
    assert PdfIngest.accepts(str(f)) and not PdfIngest.accepts(str(tmp_path / "x.epub"))
