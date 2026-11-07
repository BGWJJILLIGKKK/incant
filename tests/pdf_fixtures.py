"""Typeset test PDFs with known content (reportlab), for checking PDF ingest against the source text."""

from __future__ import annotations

from reportlab.lib.pagesizes import A5, A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, PageBreak, Spacer
from xml.sax.saxutils import escape

FOOTNOTE = "This footnote, set in small type, should not be read aloud."


def make_pdf(path, chapters: list[tuple[str, list[str]]], layout: str = "book", title: str = "Pride and Prejudice",
             author: str = "Jane Austen") -> None:
    pagesize = A5 if layout == "book" else A4
    w, h = pagesize
    margin = 42
    two_col = layout == "columns"
    gutter = 18
    if two_col:
        cw = (w - 2 * margin - gutter) / 2
        frames = [Frame(margin, margin, cw, h - 2 * margin, id="l"), Frame(margin + cw + gutter, margin, cw, h - 2 * margin, id="r")]
    else:
        frames = [Frame(margin, margin, w - 2 * margin, h - 2 * margin, id="f")]
    state = {"chapter": ""}

    def on_page(c, doc):
        n = doc.page
        c.setFont("Times-Roman", 8)
        # running header: book title on even pages, chapter title on odd; page number footer
        c.drawCentredString(w / 2, h - 26, (title.upper() if n % 2 == 0 else state["chapter"]))
        c.drawCentredString(w / 2, 14, str(n))
        if n == 3:
            c.setFont("Times-Roman", 7)
            c.drawString(margin, 26, "* " + FOOTNOTE)

    doc = BaseDocTemplate(str(path), pagesize=pagesize, title=title, author=author,
                          leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin)
    doc.addPageTemplates([PageTemplate(id="p", frames=frames, onPage=on_page)])
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=11 if not two_col else 9.5,
                          leading=14 if not two_col else 12, alignment=TA_JUSTIFY,
                          firstLineIndent=0 if two_col else 16, spaceAfter=6 if two_col else 0,
                          hyphenationLang="en_GB", embeddedHyphenation=1, uriWasteReduce=0.3)
    head = ParagraphStyle("head", fontName="Times-Bold", fontSize=18, leading=22, alignment=TA_CENTER, spaceAfter=18)

    class Chapter(Paragraph):
        def __init__(self, text, style):
            super().__init__(escape(text), style)
            self._title = text

        def draw(self):
            state["chapter"] = self._title.upper()
            super().draw()

    story = []
    for i, (t, paras) in enumerate(chapters):
        if i:
            story.append(PageBreak())
        story += [Spacer(1, 24), Chapter(t, head)]
        story += [Paragraph(escape(p.replace("*", "")), body) for p in paras]
    doc.build(story)
