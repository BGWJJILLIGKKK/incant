"""incant_local's Docling method without its heavy packages: markdown cleanup, inheritance, auto-detection."""

import importlib.util

from incant.pipeline import _auto, discover, load_method_class, parent_methods
from incant.stages.ingest.docling.docling import paragraphs_from_markdown

INGEST = {s.name: s for s in discover()}["ingest"]

def test_docling_running_headers_dropped():
    md = ("## A CHRISTMAS CAROL\n\nScrooge sat.\n\n<!-- image -->\n\n## STAVE ONE\n\nMarley was dead.\n\n"
          "## A CHRISTMAS CAROL\n\nTo begin with.")
    assert paragraphs_from_markdown(md, max_repeats=2) == ["Scrooge sat.", "# STAVE ONE", "Marley was dead.", "To begin with."]


def test_docling_is_a_pdf_child_and_stays_out_of_auto_without_docling(tmp_path):
    assert parent_methods(INGEST, load_method_class(INGEST, "docling")) == ["pdf"]
    if importlib.util.find_spec("docling") is None:
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        assert _auto(INGEST, str(pdf)) == "pdf"  # lean install: pdf explains how to OCR
