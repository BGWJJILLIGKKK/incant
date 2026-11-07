"""A failed run removes a book it created; books that existed before, or have audio, are kept."""

import pytest

from incant.cli import main
from incant.config import load_config
from incant.pipeline import cleanup_failed
from incant.stages.base import Context
from incant.workdir import WorkDir

TEXT = "Title: Short Book\n\nChapter 1\n\nIt was short. “Yes,” said Bo.\n\nChapter 2\n\nThe end came.\n"


@pytest.fixture
def setup(tmp_path):
    src = tmp_path / "short.txt"
    src.write_text(TEXT)
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n[pipeline]\nparse = "basic"\nattribute = "narrator"\n'
                    'normalize = "rules"\nsynthesize = "dummy"\n')
    return tmp_path, src, conf


def test_failed_new_book_is_removed(setup, capsys):
    tmp, src, conf = setup
    # speakers needs characters.json, which parse=basic doesn't make: fails after ingest + parse
    with pytest.raises(RuntimeError, match="characters.json"):
        main([str(src), "speakers", "-c", str(conf), "--until", "attribute"])
    assert "removed" in capsys.readouterr().out
    assert not (tmp / "work" / "short-book").exists()


def test_existing_book_is_kept(setup):
    tmp, src, conf = setup
    assert main([str(src), "-c", str(conf), "--until", "parse"]) == 0
    with pytest.raises(RuntimeError):  # re-ingest, then fail
        main([str(src), "speakers", "-c", str(conf), "--until", "attribute"])
    assert (tmp / "work" / "short-book" / "meta.json").exists()


def test_cleanup_can_be_disabled(setup):
    tmp, src, conf = setup
    conf.write_text(conf.read_text() + '[run]\ncleanup_failed = "never"\n')
    with pytest.raises(RuntimeError):
        main([str(src), "speakers", "-c", str(conf), "--until", "attribute"])
    assert (tmp / "work" / "short-book" / "meta.json").exists()


def test_failed_batch_items_are_cleaned_individually(setup, capsys):
    tmp, src, conf = setup
    d = tmp / "in"
    d.mkdir()
    src.rename(d / "a.txt")
    (d / "b.txt").write_text(TEXT.replace("Short Book", "Other Book"))
    assert main([str(d), "speakers", "-c", str(conf), "--until", "attribute"]) == 1
    assert "batch finished: 0 ok, 2 failed" in capsys.readouterr().out
    assert not any((tmp / "work").iterdir())


def test_books_with_audio_or_outside_work_dir_are_kept(setup):
    tmp, _, conf = setup
    cfg = load_config(conf)
    wd = WorkDir.for_slug(cfg.paths.work_dir, "b")
    (wd.cache_dir / "ab").mkdir(parents=True)
    (wd.cache_dir / "ab" / "abc.wav").write_bytes(b"RIFF")
    ctx = Context(cfg=cfg, wd=wd, created=True, log=lambda m: None)
    assert not cleanup_failed(ctx, RuntimeError()) and wd.root.exists()

    outside = WorkDir(tmp / "elsewhere")
    outside.root.mkdir()
    ctx = Context(cfg=cfg, wd=outside, created=True, log=lambda m: None)
    assert not cleanup_failed(ctx, RuntimeError()) and outside.root.exists()

    (wd.cache_dir / "ab" / "abc.wav").unlink()
    ctx = Context(cfg=cfg, wd=wd, created=True, log=lambda m: None)
    assert cleanup_failed(ctx, RuntimeError()) and not wd.root.exists()
