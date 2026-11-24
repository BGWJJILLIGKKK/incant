import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from incant.cli import main
from incant.config import load_config
from incant.ebook import read_draft
from incant.pipeline import discover, read_records
from incant.schemas import BookMeta
from incant.stages.ingest.epub.epub import ingest_epub
from incant.workdir import WorkDir


@pytest.fixture
def publishing(tmp_path, monkeypatch):
    monkeypatch.setenv("INCANT_DATA", str(tmp_path))
    monkeypatch.delenv("INCANT_LLM", raising=False)
    config = tmp_path / "incant.toml"
    config.write_text('[pipeline]\nparse = "basic"\nattribute = "narrator"\nsynthesize = "dummy"\n')
    return ["--config", str(config)], load_config(config)


def test_both_modes_use_the_same_import_classes():
    audio = discover()
    ebook = discover("ebook")
    assert [stage.name for stage in ebook] == ["preingest", "ingest", "draft", "publish"]
    assert [(stage.name, stage.module.Base, stage.methods) for stage in ebook[:2]] == [
        (stage.name, stage.module.Base, stage.methods) for stage in audio[:2]
    ]


def test_ebook_and_audiobook_share_import_and_keep_edits_separate(publishing, tmp_path):
    args, cfg = publishing
    source = tmp_path / "sample.md"
    source.write_text("# Opening\nThe room was *quiet*. The wind moved.\n\n## Ending\nThe story continues.")
    assert main([*args, str(source), "--until", "ingest"]) == 0
    wd = WorkDir(cfg.paths.work_dir / "sample")
    imported = (wd.source_path.read_bytes(), wd.text_path.read_bytes(), wd.meta_path.read_bytes())
    source.unlink()  # neither branch can re-run ingestion
    assert main([*args, "--mode", "ebook", "sample", "--until", "draft"]) == 0
    draft = read_draft(wd)
    assert [c.title for c in draft.chapters] == ["Opening", "Ending"]
    draft.title = "My edited ebook"
    draft.chapters[1].paragraphs = ["Only in the ebook."]
    (wd.root / "ebook.json").write_text(draft.model_dump_json())
    assert main([*args, "--mode", "ebook", "sample", "--from", "publish"]) == 0
    assert main([*args, "sample", "--until", "attribute"]) == 0
    assert imported == (wd.source_path.read_bytes(), wd.text_path.read_bytes(), wd.meta_path.read_bytes())
    assert wd.read_meta().title == "Sample"
    assert wd.read_chapters().chapters[1].title == "Ending"
    assert not (wd.root / "output.json").exists()
    assert set(read_records(wd)) == {"ingest", "parse", "attribute"}
    assert set(read_records(wd, ebook=True)) == {"draft", "publish"}
    artifact = cfg.paths.output_dir / "ebooks" / "sample.epub"
    assert ingest_epub(artifact).title == "My edited ebook"
    with ZipFile(artifact) as epub:
        assert b"<em>quiet</em>" in epub.read("EPUB/chapter-1.xhtml")
        assert b"Only in the ebook." in epub.read("EPUB/chapter-2.xhtml")


def test_epub_input_refused_on_fresh_and_resumed_ebook_runs(publishing, tmp_path, capsys):
    args, cfg = publishing
    text = tmp_path / "source.txt"
    text.write_text("A short story with enough words to read.")
    original = tmp_path / "original.epub"
    assert main([*args, "--mode", "ebook", str(text), "-o", str(original)]) == 0
    original_bytes = original.read_bytes()
    target = tmp_path / "forbidden.epub"
    assert main([*args, "--mode", "ebook", str(original), "-o", str(target)]) == 2
    assert "same-format" in capsys.readouterr().err
    assert not target.exists() and original.read_bytes() == original_bytes
    assert main([*args, str(original), "--slug", "native-epub", "--until", "ingest"]) == 0
    wd = WorkDir(cfg.paths.work_dir / "native-epub")
    assert wd.read_meta().input_format == "epub"
    assert main([*args, "--mode", "ebook", "native-epub", "--from", "publish", "-o", str(target)]) == 2
    assert not target.exists()
    renamed = original.with_suffix(".txt")
    shutil.copyfile(original, renamed)
    assert main([*args, "--mode", "ebook", str(renamed), "-o", str(target)]) == 2
    assert not target.exists()


@pytest.mark.parametrize("input_format", [None, "docx"])
def test_converted_document_can_publish_epub(publishing, tmp_path, input_format):
    args, cfg = publishing
    wd = WorkDir(cfg.paths.work_dir / "word")
    wd.root.mkdir(parents=True)
    wd.write_meta(BookMeta(slug="word", title="Word", source=str(tmp_path / "word.docx"),
                           source_format="epub", input_format=input_format))
    wd.text_path.write_text("A missing original can still be published from its saved text.\n")
    assert main([*args, "--mode", "ebook", "word"]) == 0
    record = json.loads((wd.root / "ebook-output.json").read_text())
    assert Path(record["files"][0]).is_file()
