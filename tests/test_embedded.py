"""Text from JavaScript-built pages that carry the article as data inside <script>."""

import json

import httpx

from incant.stages.ingest.url.embedded import embedded_text, is_prose
from incant.stages.ingest.url.url import Fetcher, UrlSettings, ingest_url

STORY = [f"Paragraph {i}: the dragon walked down the quiet street, and the neighbours stared. “Good morning,” "
         f"she said, as if nothing were unusual at all. Nobody answered her." for i in range(40)]
STORY_HTML = "".join(f"<p>{p}</p>" for p in STORY)
COMMENT = "Great story, loved it! " * 20  # 80 words: a comment, not the article
SHELL = '<html><head><title>{t}</title></head><body><div id="root"></div>{s}</body></html>'


def fetch(page):
    return Fetcher(UrlSettings(netrc=False), transport=httpx.MockTransport(lambda r: httpx.Response(200, html=page)))


def test_next_data():
    data = {"props": {"pageProps": {"post": {"title": "T", "body": STORY_HTML, "comments": [COMMENT]}}}}
    page = SHELL.format(t="Next", s=f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>')
    ing = ingest_url("https://next.test/p", fetch(page))
    assert len(ing.paragraphs) == 40 and ing.paragraphs[0].startswith("Paragraph 0:")
    assert any("embedded" in w for w in ing.warnings)


def test_react_router_stream_double_encoded():
    flat = ["loaderData", {"_1": 2}, "content", STORY_HTML.replace("</p><p>", "<br />\n<br />\n"), COMMENT]
    inner = json.dumps(flat)
    script = f"window.__reactRouterContext.streamController.enqueue({json.dumps(inner)});"
    text = embedded_text(SHELL.format(t="RR", s=f"<script>{script}</script>"), 400)
    assert text and text.count("\n\n") == 39 and "<br" not in text


def test_json_ld_article_body():
    ld = {"@context": "https://schema.org", "@type": "Article", "articleBody": " ".join(STORY)}
    page = SHELL.format(t="LD", s=f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    assert embedded_text(page, 400).startswith("Paragraph 0:")


def test_not_prose_or_too_short():
    blob = "QUFBQUFB" * 400  # base64-ish
    urls = " ".join(f"https://cdn.test/{i}.js" for i in range(500))
    tags = ", ".join(["dragon", "romance", "suburbia", "humor"] * 150)
    data = {"a": COMMENT, "b": blob, "c": urls, "d": tags}
    page = SHELL.format(t="X", s=f'<script type="application/json">{json.dumps(data)}</script>')
    assert embedded_text(page, 400) is None
    assert not is_prose(COMMENT, 400)


def test_markup_text_still_preferred():
    article = "<html><body><article>" + STORY_HTML + "</article><script>var x = " + json.dumps(
        {"other": " ".join(STORY[:20]) + " different"}) + "</script></body></html>"
    ing = ingest_url("https://plain.test/a", fetch(article))
    assert not any("embedded" in w for w in ing.warnings)
