"""PDFs without a usable text layer (scans), read with Docling's layout analysis and OCR — child of `pdf`.

Auto-detection picks it only for PDFs whose text layer is missing or nearly empty, and only where Docling is
installed (the incant_local image, or the torch extra); otherwise `pdf` explains how to OCR the file. Name it
explicitly (`ingest=docling`) to use it on any PDF, e.g. one with a poor or multi-column layout.
Running page headers that Docling reports as headings (a book or chapter title on every page) are dropped:
a heading that repeats (`max_repeats`, default 2) is not a chapter. Docling (MIT), CPU, ~7 s per page.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections import Counter
from pathlib import Path

from ....normalize import SCENE_BREAK, clean_paragraph, clean_unicode, heading
from ..base import Ingested
from ..pdf.pdf import PdfIngest, _info, read_pages

__all__ = ["DoclingIngest"]

from pydantic import BaseModel


class DoclingSettings(BaseModel):
    ocr: bool = True
    max_repeats: int = 2  # a heading seen this many times is a running header, not a chapter


def has_text_layer(path: Path, min_chars: int = 200) -> bool:
    try:
        pages = read_pages(path)
    except Exception:  # noqa: BLE001 — unreadable by pdfminer: let docling try
        return False
    return sum(len(ln.text) for p in pages for ln in p.lines) >= min_chars


def paragraphs_from_markdown(md: str, max_repeats: int) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", clean_unicode(md)) if b.strip()]
    heads = Counter(re.sub(r"^#+\s*", "", b).casefold() for b in blocks if b.startswith("#"))
    out = []
    for b in blocks:
        if b.startswith("<!--"):  # image placeholders
            continue
        if b.startswith("#"):
            text = re.sub(r"^#+\s*", "", b)
            if heads[text.casefold()] >= max_repeats:
                continue  # running header
            out.append(heading(text) if text.strip() else SCENE_BREAK)
            continue
        out.append(clean_paragraph(b))
    return [p for p in out if p]


class DoclingIngest(PdfIngest):
    LICENSE = "project code; Docling and its models (MIT)"
    OUTPUT_TERMS = "none"
    Settings = DoclingSettings
    settings: DoclingSettings

    @classmethod
    def accepts(cls, source: str) -> bool:
        if not super().accepts(source) or importlib.util.find_spec("docling") is None:
            return False
        return not has_text_layer(Path(source))  # a PDF with text stays with `pdf`

    def load(self, source: str) -> Ingested:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError:
            raise RuntimeError("ingest=docling needs the torch extra or the incant_local image") from None
        path = Path(source)
        artifacts = os.environ.get("INCANT_DOCLING_DIR")  # baked models (incant_local); else downloaded
        opts = PdfPipelineOptions(do_ocr=self.settings.ocr, do_table_structure=False,
                                  artifacts_path=artifacts if artifacts and Path(artifacts).is_dir() else None)
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        md = conv.convert(str(path)).document.export_to_markdown()
        paragraphs = paragraphs_from_markdown(md, self.settings.max_repeats)
        if sum(len(p.split()) for p in paragraphs) < 50:
            raise RuntimeError(f"{path.name}: Docling found almost no text (OCR off, or not a text document?)")
        info = _info(path)
        return Ingested(title=info.get("title") or path.stem.replace("_", " ").replace("-", " ").title(),
                        author=info.get("author"), paragraphs=paragraphs, source_format="pdf",
                        warnings=["text from Docling layout analysis and OCR: check it for recognition errors"])
