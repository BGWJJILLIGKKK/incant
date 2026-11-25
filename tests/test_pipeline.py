"""Discovery, CLI resolution, and an end-to-end run: synthetic EPUB -> ... -> m4b (dummy TTS)."""

import json
import shutil
import subprocess

import pytest

from incant.cli import main
from incant.config import Config
from incant.pipeline import ResolveError, discover, load_method_class, resolve

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_epub(path):
    """A small EPUB 3 (with an NCX too) like Project Gutenberg's: title page, two chapters, a license page."""
    docs = [
        ("Title", "c0.xhtml", "<h1>Test Book</h1><p>by A. Writer</p>"),
        ("Chapter One", "c1.xhtml", "<h1>Test Book</h1><h2>Chapter One</h2><p>It began. “Hello,” said Ann, “there.”</p>"
                                    '<div class="figcenter"><p class="caption">A caption to skip</p></div>'
                                    '<p><img alt="T" src="x.png"/>he end of one.</p>'),
        ("Chapter Two", "c2.xhtml", "<h2>Chapter Two</h2><p>Second <i>chapter</i> text.</p><table><tr><td>skip me</td></tr></table>"),
        ("License", "c3.xhtml", '<section id="pg-footer"><h2>License</h2><p>Boilerplate.</p></section>'),
    ]
    write_epub(path, "Test Book", "A. Writer", [(name, title, body) for title, name, body in docs])


def write_epub(path, title, author, docs, *, ncx=True, nav=True, opf_dir="EPUB", extra_manifest="", extra_meta=""):
    """Write an EPUB with the standard library: docs = [(file name, TOC title, body html)]."""
    import zipfile

    x = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
    manifest = "".join(f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, (n, _, _) in enumerate(docs))
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    if nav:
        manifest += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        spine = '<itemref idref="nav"/>' + spine
    if ncx:
        manifest += '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
    base = f"{opf_dir}/" if opf_dir else ""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   f'<rootfile full-path="{base}content.opf" media-type="application/oebps-package+xml"/>'
                   "</rootfiles></container>")
        z.writestr(f"{base}content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="id">test-1</dc:identifier><dc:title>{title}</dc:title>'
                   f"<dc:creator>{author}</dc:creator><dc:language>en</dc:language>{extra_meta}</metadata>"
                   f'<manifest>{manifest}{extra_manifest}</manifest><spine{" toc=\"ncx\"" if ncx else ""}>{spine}</spine></package>')
        for name, toc_title, body in docs:
            z.writestr(f"{base}{name}", f"<html {x}><head><title>{toc_title}</title></head><body>{body}</body></html>")
        if nav:
            links = "".join(f'<li><a href="{n}">{t}</a></li>' for n, t, _ in docs)
            z.writestr(f"{base}nav.xhtml", f'<html {x}><body><nav epub:type="toc"><ol>{links}</ol></nav></body></html>')
        if ncx:
            points = "".join(f'<navPoint id="p{i}"><navLabel><text>{t}</text></navLabel><content src="{n}"/></navPoint>'
                             for i, (n, t, _) in enumerate(docs))
            z.writestr(f"{base}toc.ncx", '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                       f"<navMap>{points}</navMap></ncx>")


@pytest.fixture
def setup(tmp_path):
    src = tmp_path / "t.epub"
    make_epub(src)
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n[pipeline]\nparse = "basic"\nattribute = "narrator"\nnormalize = "rules"\nsynthesize = "dummy"\n')
    return tmp_path, src, conf


# ------------------------------------------------------------------ discovery / resolution


def test_discovery_finds_stages_and_methods_in_order():
    stages = discover()
    assert [s.name for s in stages] == ["preingest", "ingest", "parse", "attribute", "normalize", "synthesize", "output"]
    by = {s.name: s for s in stages}
    assert {"epub", "txt", "url"} <= set(by["ingest"].methods)
    assert "bookmark" in by["preingest"].methods
    for s in stages:
        for m in s.methods:
            cls = load_method_class(s, m)
            assert issubclass(cls, s.module.Base) and cls.name == m


def test_resolve_out_of_order_explicit_and_output_path(setup):
    _, src, _ = setup
    stages = discover()
    steps, out = resolve(["m4b", "dummy", "parse=basic", "narrator", "rules", "final.m4b"], str(src), Config(), stages)
    plan = {s.stage.name: (s.method, s.why) for s in steps}
    assert plan["ingest"] == ("epub", "auto")
    assert plan["synthesize"] == ("dummy", "cli") and plan["output"] == ("m4b", "cli")
    assert str(out) == "final.m4b"


def test_resolve_errors(setup):
    _, src, _ = setup
    stages = discover()
    with pytest.raises(ResolveError, match="did you mean indextts"):
        resolve(["indexts"], str(src), Config(), stages)
    with pytest.raises(ResolveError, match="already set"):
        resolve(["dummy", "indextts"], str(src), Config(), stages)
    steps, _ = resolve([], str(src), Config(), stages)  # several TTS methods, no config: the stage's DEFAULT
    assert {s.stage.name: s.why for s in steps}["synthesize"] == "default"


def test_resolve_uses_config_default(setup):
    _, src, _ = setup
    steps, _ = resolve([], str(src), Config(pipeline={"parse": "basic", "attribute": "narrator", "normalize": "rules", "synthesize": "dummy"}), discover())
    assert [(s.stage.name, s.why) for s in steps if s.stage.name == "synthesize"] == [("synthesize", "config")]


def test_tags_handles_narration_without_named_speakers(setup):
    from incant.workdir import WorkDir

    tmp, _, conf = setup
    src = tmp / "essay.txt"
    src.write_text("The sun rose. The rain stopped.\n")
    assert main([str(src), "tags", "-c", str(conf), "--until", "normalize"]) == 0
    speech = WorkDir(tmp / "work" / "essay").read_speech("0001")
    assert speech.spans and all(span.speaker == "narrator" for span in speech.spans)


def test_full_attribution_fills_chapters_after_partial_run(setup):
    from incant.workdir import WorkDir

    tmp, src, conf = setup
    args = [str(src), "tags", "-c", str(conf), "--until", "normalize"]
    wd = WorkDir(tmp / "work" / "test-book")
    for _ in range(2):
        assert main([*args, "--chapters", "1"]) == 0
    assert wd.read_speech("0001") is not None
    assert not wd.spans_path("0002").exists()
    assert main(args) == 0
    assert wd.read_spans("0002")
    assert wd.read_speech("0002") is not None


@pytest.mark.parametrize(("options", "message"), [
    (["--chapters", "nope"], "invalid chapter selection"),
    (["--chapters", "3-1"], "invalid chapter selection"),
    (["--chapters", "1-"], "invalid chapter selection"),
    (["--chapters", "0"], "invalid chapter selection"),
    (["--until", "unknown"], "unknown stage"),
    (["--from", "output", "--until", "parse"], "comes after"),
])
@pytest.mark.parametrize("batch", [False, True])
def test_dry_run_rejects_invalid_options(setup, capsys, batch, options, message):
    tmp, src, conf = setup
    assert main([str(tmp if batch else src), "-c", str(conf), "--dry-run", *options]) == 2
    assert message in capsys.readouterr().err


def test_run_reports_chapter_missing_from_book(setup, capsys):
    _, src, conf = setup
    assert main([str(src), "-c", str(conf), "--until", "attribute", "--chapters", "3"]) == 2
    assert "unknown chapter(s): 0003" in capsys.readouterr().err


# ------------------------------------------------------------------ end to end


def test_epub_ingest_structure(setup):
    tmp, src, conf = setup
    assert main([str(src), "--until", "parse", "-c", str(conf)]) == 0
    root = tmp / "work" / "test-book"
    text = (root / "text.md").read_text()
    assert "# Chapter One" in text and "# Chapter Two" in text
    assert "# Test Book" not in text  # repeated book title is not a chapter boundary
    for gone in ("caption to skip", "skip me", "Boilerplate", "License"):
        assert gone not in text
    assert "The end of one." in text  # image drop-cap restored
    assert "*chapter*" in text
    chapters = json.loads((root / "chapters.json").read_text())["chapters"]
    assert [c["title"] for c in chapters] == ["Chapter One", "Chapter Two"]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_end_to_end_dummy_and_resume(setup, capsys):
    tmp, src, conf = setup
    out = tmp / "book.m4b"
    assert main([str(src), "epub", "basic", "narrator", "rules", "dummy", "m4b", str(out), "-c", str(conf)]) == 0
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(out)],
                                      capture_output=True, check=True).stdout)
    assert [c["tags"]["title"] for c in probe["chapters"]] == ["Chapter One", "Chapter Two"]
    assert probe["format"]["tags"]["artist"] == "A. Writer"

    capsys.readouterr()
    assert main(["test-book", "-c", str(conf), "-o", str(out)]) == 0  # resume by slug: nothing upstream changed
    log = capsys.readouterr().out
    assert "[parse] basic: up to date" in log and "[attribute] narrator: up to date" in log

    ch = tmp / "work" / "test-book" / "chapters" / "0001.txt"
    ch.write_text(ch.read_text().replace("It began.", "It started."))
    assert main(["test-book", "-c", str(conf), "-o", str(out)]) == 0  # chapter edit -> attribute reruns, parse keeps the edit
    log = capsys.readouterr().out
    assert "[parse] basic: up to date" in log and "[attribute] narrator\n" in log
    assert "It started." in ch.read_text()


def test_same_voice_spans_merge_into_one_segment(setup):
    from incant.config import load_config
    from incant.pipeline import discover, instantiate
    from incant.schemas import Cast
    from incant.stages.synthesize.render import Renderer
    from incant.workdir import WorkDir

    tmp, src, conf = setup
    assert main([str(src), "--until", "attribute", "-c", str(conf)]) == 0
    cfg = load_config(conf)
    wd = WorkDir.for_slug(cfg.paths.work_dir, "test-book")
    dummy = instantiate(cfg, {s.name: s for s in discover()}["synthesize"], "dummy")
    ch = wd.read_chapters().chapters[0]

    # single voice: quote + tag + quote in one paragraph -> one segment
    one = Renderer(cfg, wd, dummy, dummy.voices(), Cast(roles={"narrator": "n"}, default_voice="n"), strict_voices=False)
    texts = [s.text for s in one.plan(ch)]
    assert "It began. Hello, said Ann, there." in texts

    # distinct voices for narration vs dialogue -> split again
    spans = wd.read_spans(ch.id)
    for s in spans:
        if s.kind == "dialogue":
            s.speaker = "ann"
    wd.write_spans(ch.id, spans)
    two = Renderer(cfg, wd, dummy, dummy.voices(), Cast(roles={"narrator": "n", "ann": "a"}), strict_voices=False)
    assert [(s.voice, s.text) for s in two.plan(ch)][1:4] == [("n", "It began."), ("a", "Hello,"), ("n", "said Ann,")]


def test_locked_spans_and_say_overrides_survive_reruns(setup):
    import json

    from incant.workdir import WorkDir

    tmp, src, conf = setup
    assert main([str(src), "--until", "normalize", "-c", str(conf)]) == 0
    wd = WorkDir(tmp / "work" / "test-book")
    spans = wd.read_spans("0001")
    q = next(s for s in spans if s.kind == "dialogue")
    q.speaker, q.locked = "ann", True
    wd.write_spans("0001", spans)
    (wd.root / "say_overrides.json").write_text(json.dumps({q.id: {"text": q.text, "say": "Hullo"}}))
    assert main(["test-book", "--from", "attribute", "--until", "normalize", "--force", "attribute", "-c", str(conf)]) == 0
    again = {s.id: s for s in wd.read_spans("0001")}
    assert again[q.id].speaker == "ann" and again[q.id].locked
    assert {s.id: s for s in wd.read_speech("0001").spans}[q.id].say == "Hullo"


def test_parse_removes_prose_backslashes_before_attribution(setup):
    from incant.workdir import WorkDir

    tmp, _, conf = setup
    src = tmp / "escaped.txt"
    src.write_text(r'''\"I don\'t know,\" said Alice.''' + "\\\n\n" + r"The file is C:\data\story.txt." + "\n")
    assert main([str(src), "-c", str(conf), "--until", "attribute"]) == 0
    wd = WorkDir(tmp / "work" / "escaped")
    spans = wd.read_spans("0001")
    assert [s.text for s in spans if s.kind == "dialogue"] == ['"I don\'t know,"']
    assert wd.read_chapter_text(wd.read_chapters().chapters[0].file) == (
        '"I don\'t know," said Alice.\n\n' + r"The file is C:\data\story.txt." + "\n"
    )


def test_review_reassigns_speakers_across_chapters_and_locks_edits(setup):
    from incant.config import load_config
    from incant.review.server import Book
    from incant.schemas import Character, CharacterList
    from incant.workdir import WorkDir

    tmp, src, conf = setup
    assert main([str(src), "-c", str(conf), "--until", "attribute"]) == 0
    wd = WorkDir(tmp / "work" / "test-book")
    wd.write_characters(CharacterList(characters=[Character(id="ann", canonical_name="Ann"),
                                                  Character(id="anne", canonical_name="Anne")]))
    for chapter in wd.read_chapters().chapters:
        spans = wd.read_spans(chapter.id)
        spans[0].speaker = "ann"
        wd.write_spans(chapter.id, spans)
    book = Book(load_config(conf), wd)
    assert book.reassign_speaker({"source": "ann", "target": "anne", "chapter_id": "0001"}) == {
        "moved": 1, "chapters": ["0001"]
    }
    assert wd.read_spans("0002")[0].speaker == "ann"
    assert book.reassign_speaker({"source": "ann", "target": "anne"}) == {"moved": 1, "chapters": ["0002"]}
    assert main(["test-book", "-c", str(conf), "--from", "attribute", "--until", "attribute", "--force", "attribute"]) == 0
    for cid in ("0001", "0002"):
        span = wd.read_spans(cid)[0]
        assert span.speaker == "anne" and span.locked


def test_first_review_audition_prepares_the_cast(setup, monkeypatch):
    from incant.audio import pcm_to_wav
    from incant.config import load_config
    from incant.review.server import Book
    from incant.workdir import WorkDir

    tmp, src, conf = setup
    assert main([str(src), "-c", str(conf), "--until", "normalize"]) == 0
    cfg = load_config(conf)
    cfg.pipeline["synthesize"] = "kokoro"
    cfg.audio.level_match = False
    wd = WorkDir(tmp / "work" / "test-book")
    book = Book(cfg, wd)
    voices = []

    def synthesize(text, voice, emotion, variant=0):
        voices.append(voice.id)
        return pcm_to_wav(b"\0\0" * 1000, cfg.audio.sample_rate)

    assert wd.read_cast() is None
    monkeypatch.setattr(book.synth(), "synthesize", synthesize)
    span = wd.read_spans("0001")[0]
    result = book.render_span(span.id)
    assert voices == [book.synth().narrator()] == [result["voice"]]
    assert wd.read_cast().roles["narrator"] == result["voice"]
    assert book.segment_audio(result["key"]).is_file()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_review_book_api(setup):
    import tomllib

    from incant.config import load_config
    from incant.review.server import Book
    from incant.workdir import WorkDir

    tmp, src, conf = setup
    assert main([str(src), "-c", str(conf), "-o", str(tmp / "o.m4b")]) == 0
    cfg = load_config(conf)
    cfg.pipeline["synthesize"] = "kokoro"
    cfg.pipeline["normalize"] = "none"
    book = Book(cfg, WorkDir(tmp / "work" / "test-book"))
    info = book.book()
    assert info["synthesize"] == "dummy"
    assert info["normalize"] == "rules"
    assert [c["id"] for c in info["chapters"]] == ["0001", "0002"] and all(c["rendered"] for c in info["chapters"])
    ch = book.chapter("0001")
    spans = [s for p in ch["paragraphs"] for s in p]
    q = next(s for s in spans if s["kind"] == "dialogue")
    assert q["say"] and q["segment"]["rendered"]
    assert book.segment_audio(q["segment"]["key"]) is not None and book.chapter_audio("0001") is not None
    assert book.segment_audio("../../etc/passwd") is None

    saved = book.save_span({"id": q["id"], "speaker": "narrator", "emotion": {"text": "wry", "alpha": 0.4}, "say": "Hi"})
    assert saved["locked"] and saved["emotion"]["text"] == "wry"
    edited = next(s for p in book.chapter("0001")["paragraphs"] for s in p if s["id"] == q["id"])
    assert edited["say"] == "Hi"
    assert edited["segment"]["key"] != q["segment"]["key"]
    audition = book.render_span(q["id"])
    assert audition["key"] == edited["segment"]["key"] and "Hi" in audition["text"]
    book.render_chapter("0001")
    assert book.segment_audio(audition["key"]).exists()
    book.save_span({"id": q["id"], "omit": True})
    hidden = next(s for p in book.chapter("0001")["paragraphs"] for s in p if s["id"] == q["id"])
    assert hidden["omitted"] and hidden["text"] == q["text"]
    assert hidden["segment"] is None
    with pytest.raises(KeyError, match="no segment"):
        book.render_span(q["id"])
    assert main(["test-book", "-c", str(conf), "--from", "normalize", "--until", "synthesize", "--chapters", "1"]) == 0
    assert next(s for s in book.wd.read_speech("0001").spans if s.id == q["id"]).omitted
    assert all(q["id"] not in seg.span_ids for seg in book.renderer().plan(book.wd.read_chapters().chapters[0]))
    book.save_span({"id": q["id"], "omit": False})
    kept = next(s for p in book.chapter("0001")["paragraphs"] for s in p if s["id"] == q["id"])
    assert not kept.get("omitted") and kept["say"] == "Hi"
    book.save_span({"id": q["id"], "say": None})
    restored = next(s for p in book.chapter("0001")["paragraphs"] for s in p if s["id"] == q["id"])
    assert restored["say"] == q["say"] and not restored["say_overridden"]
    with pytest.raises(ValueError):
        book.save_span({"id": q["id"], "speaker": "nobody"})
    assert book.save_cast({"character": "narrator", "voice": None})["roles"].get("narrator") is None
    book.save_lexicon({"Ann": "Anne", " ": "x"})
    assert tomllib.loads((tmp / "work" / "test-book" / "lexicon.toml").read_text())["words"] == {"Ann": "Anne"}


def test_review_edits_the_cast_for_the_recorded_backend(tmp_path):
    from incant.review.server import Book
    from incant.schemas import Cast
    from incant.workdir import WorkDir

    wd = WorkDir(tmp_path)
    (tmp_path / "pipeline.json").write_text(json.dumps({"synthesize": {"method": "openai"}}))
    active = Cast(engine="kokoro", roles={"narrator": "af_bella"})
    wd.write_cast(active)
    saved_path = wd.cast_path_for("openai-kokoro")
    wd.write_cast(Cast(engine="openai-kokoro", roles={"narrator": "af_heart"}), saved_path)
    book = Book(Config(pipeline={"synthesize": "kokoro"}), wd)
    assert book.save_cast({"character": "alice", "voice": "af_heart"})["roles"]["narrator"] == "af_heart"
    assert wd.read_cast() == active
    assert wd.read_cast(saved_path).roles["alice"] == "af_heart"


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_batch_directory(setup, capsys):
    tmp, src, conf = setup
    books = tmp / "books"
    books.mkdir()
    src.rename(books / "a.epub")
    (books / "b.txt").write_text("Title: Little Book\n\nChapter 1\n\nIt was short. “Yes,” said Bo.\n\nChapter 2\n\nThe end came.\n")
    (books / "notes.xyz").write_bytes(b"x")       # unsupported: skipped
    (books / "c.epub").write_bytes(b"not an epub")  # fails: reported, batch continues
    outs = tmp / "audiobooks"
    rc = main([str(books), "-c", str(conf), "-o", str(outs)])
    log = capsys.readouterr()
    assert rc == 1  # one failure
    assert "skip: notes.xyz" in log.out and "batch finished: 2 ok, 1 failed" in log.out
    assert sorted(str(p.relative_to(outs)) for p in outs.rglob("*.m4b")) == [
        "A. Writer/Test Book/Test Book.m4b", "Unknown Author/Little Book/Little Book.m4b"]
    assert main([str(books), "-c", str(conf), "--slug", "x"]) == 2
    assert main([str(books), "-c", str(conf), "-o", str(tmp / "one.m4b")]) == 2


def test_version_flag(capsys):
    from incant import __version__

    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and capsys.readouterr().out.strip() == f"incant {__version__}"
