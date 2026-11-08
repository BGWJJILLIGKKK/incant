from incant.normalize import paragraphs_from_plain, render_text_md
from incant.stages.ingest.gutenberg.gutenberg import strip_gutenberg
from incant.schemas import SpanKind
from incant.stages.attribute.spans import reconstruct, split_chapter, split_paragraph
from incant.stages.parse.chapters import split_chapters
from incant.textprep import chunk, speakable

# ------------------------------------------------------------------ normalize


def test_plain_paragraphs_unwrap_and_italics():
    paras = paragraphs_from_plain("It was a _dark_ and\nstormy night.\n\n\n***\n\nNext.")
    assert paras == ["It was a *dark* and stormy night.", "* * *", "Next."]


def test_gutenberg_strip_keeps_heading_inside_illustration():
    raw = "junk\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n[Illustration: A man\n\nChapter I.]\n\nBody.\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicense"
    out = strip_gutenberg(raw)
    assert "Chapter I." in out and "A man" not in out and "license" not in out and "junk" not in out


# ------------------------------------------------------------------ chapters


def _text(*paras):
    return render_text_md(list(paras))


def test_split_by_pattern_drops_toc_and_short_front_matter():
    t = _text("Title Page", "CHAPTER I.", "CHAPTER II.", "CHAPTER III.",
              "CHAPTER I.", "one " * 50, "CHAPTER II.", "two " * 50)
    chunks, method, notes = split_chapters(t)
    assert method == "pattern"
    assert [c.title for c in chunks] == ["CHAPTER I.", "CHAPTER II."]
    assert any("table of contents" in n for n in notes)


def test_headings_fold_parts_and_drop_other_empty_headings():
    t = _text("# Contents", "# Part One", "# Chapter 1", "Some words. " * 5, "# Chapter 2", "More words. " * 5)
    chunks, method, notes = split_chapters(t)
    assert method == "headings"
    assert [c.title for c in chunks] == ["Part One: Chapter 1", "Chapter 2"]
    assert any("Contents" in n for n in notes)


def test_no_structure_is_single_chapter():
    chunks, method, _ = split_chapters(_text("Just one paragraph.", "And another."))
    assert method == "single" and len(chunks) == 1


# ------------------------------------------------------------------ spans


def test_dialogue_tag_split():
    parts = split_paragraph("“Hello,” she said, “come in.”")
    assert [k for k, _ in parts] == [SpanKind.dialogue, SpanKind.narration, SpanKind.dialogue]
    assert "".join(t for _, t in parts) == "“Hello,” she said, “come in.”"


def test_unspeakable_narration_folds_into_neighbour():
    parts = split_paragraph("“Yes.” “No.”")
    assert [k for k, _ in parts] == [SpanKind.dialogue, SpanKind.dialogue]
    assert "".join(t for _, t in parts) == "“Yes.” “No.”"


def test_straight_quotes_and_unclosed_multiparagraph_quote():
    parts = split_paragraph('He said "go" now.')
    assert [k for k, _ in parts] == [SpanKind.narration, SpanKind.dialogue, SpanKind.narration]
    parts = split_paragraph("“This quote runs on to the next paragraph")
    assert parts == [(SpanKind.dialogue, "“This quote runs on to the next paragraph")]


def test_span_invariant_and_ids():
    text = "Narration first.\n\n“Speech,” said A. “More.”\n\n* * *\n\nEnd."
    spans = split_chapter("0007", text)
    assert reconstruct(spans) == text
    assert spans[0].id == "0007-00001" and spans[0].speaker == "narrator"
    assert all(s.speaker is None for s in spans if s.kind is SpanKind.dialogue)


# ------------------------------------------------------------------ textprep


def test_speakable_strips_markup_and_quotes():
    assert speakable("“We are *not* here,”") == "We are not here,"
    assert speakable(" , ") == ""
    assert speakable("It doesn\\'t matter,\\") == "It doesn't matter,"
    assert speakable("a \\\"microfiction\\\" piece, \\#3") == "a microfiction piece, #3"


def test_chunk_respects_abbreviations_and_budget():
    text = "Mr. Bingley arrived. " + "This is a fairly long sentence about nothing in particular. " * 10
    chunks = chunk(text, 120)
    assert chunks[0].startswith("Mr. Bingley arrived.")
    assert all(len(c) <= 120 for c in chunks)
    assert " ".join(chunks).split() == speakable(text).split()


def test_chunk_hard_splits_long_sentence():
    s = "word, " * 100
    chunks = chunk(s, 50)
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks).split() == speakable(s).split()


# ------------------------------------------------------------------ audio / render helpers


def test_trim_silence_keeps_padding():
    import array
    import io
    import wave

    from incant.audio import pcm_to_wav, trim_silence

    rate = 22050
    a = array.array("h", [0] * rate + [8000] * 100 + [0] * rate)  # 1 s silence, burst, 1 s silence
    out = trim_silence(pcm_to_wav(a.tobytes(), rate), pad_ms=40)
    with wave.open(io.BytesIO(out)) as w:
        n = w.getnframes()
    assert n == 100 + 2 * (rate * 40 // 1000)


def test_front_matter_fragments_and_contents_dropped():
    t = _text("# A CHRISTMAS CAROL", "IN PROSE", "# A Ghost Story", "BY", "# CHARLES DICKENS", "Preface. " * 60,
              "# CONTENTS", "Stave One 1 Stave Two 20 " * 4, "# STAVE ONE.", "marley " * 100)
    chunks, _, notes = split_chapters(t)
    assert [c.title for c in chunks] == ["CHARLES DICKENS", "STAVE ONE."]
    assert any("table of contents" in n for n in notes) and any("leading fragment" in n for n in notes)
