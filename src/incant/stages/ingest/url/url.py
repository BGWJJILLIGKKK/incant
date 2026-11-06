"""Single-page URL ingest via trafilatura. Multi-page serial crawling is future work."""

from __future__ import annotations

import trafilatura

from ....normalize import clean_unicode, paragraphs_from_plain
from ..base import Ingested, IngestMethod

__all__ = ["UrlIngest"]


def ingest_url(url: str) -> Ingested:
    html = trafilatura.fetch_url(url)
    if not html:
        raise RuntimeError(f"could not fetch {url}")
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True)
    if not text:
        raise RuntimeError(f"no main text extracted from {url}")
    md = trafilatura.extract_metadata(html)
    # trafilatura emits one paragraph per line; make them blank-line separated
    text = "\n\n".join(clean_unicode(text).splitlines())
    return Ingested(
        title=(md.title if md and md.title else url),
        author=(md.author if md else None),
        paragraphs=paragraphs_from_plain(text),
        source_format="url",
        warnings=["URL ingest reads a single page only."],
    )


class UrlIngest(IngestMethod):
    """A single web page, main text extracted with trafilatura."""

    @classmethod
    def accepts(cls, source: str) -> bool:
        return source.startswith(("http://", "https://"))

    def load(self, source: str) -> Ingested:
        return ingest_url(source)
