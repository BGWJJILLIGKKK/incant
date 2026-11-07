from incant.qa import Check, edit_distance, words


def test_words_normalise_case_punctuation_hyphens():
    assert words("“To-morrow fortnight,” said Mr. Bennet’s wife.") == ["tomorrow", "fortnight", "said", "mr", "bennets", "wife"]


def test_edit_distance_and_short_line_tolerance():
    assert edit_distance("a b c d".split(), "a x c".split()) == 2
    assert Check(wer=1.0, edits=1, heard="").ok(0.25)          # one-word line misheard: whisper noise
    assert not Check(wer=0.5, edits=3, heard="").ok(0.25)


def test_renderer_retries_failed_take_and_keeps_best(tmp_path):
    from incant.config import Config, PathsConfig
    from incant.pipeline import discover, instantiate
    from incant.qa import Check
    from incant.schemas import Cast, Segment
    from incant.stages.synthesize.render import Renderer
    from incant.workdir import WorkDir

    class FakeQA:
        def __init__(self):
            self.calls = 0

        def check(self, wav, text):
            self.calls += 1
            return Check(wer=0.9, edits=5, heard="garbled") if self.calls == 1 else Check(wer=0.0, edits=0, heard=text)

    cfg = Config(paths=PathsConfig(work_dir=tmp_path))
    dummy = instantiate(cfg, {s.name: s for s in discover()}["synthesize"], "dummy")
    takes = []
    orig = dummy.synthesize
    dummy.synthesize = lambda *a, variant=0, **k: (takes.append(variant), orig(*a, **k))[1]
    wd = WorkDir(tmp_path / "book")
    qa = FakeQA()
    r = Renderer(cfg, wd, dummy, dummy.voices(), Cast(roles={}), strict_voices=False, qa=qa)
    seg = Segment(span_ids=["x"], text="hello there my friend", voice=None, cache_key="ab" * 16, pause_after_ms=0)
    assert r.synthesize([seg]) == []
    assert takes == [0, 1] and qa.calls == 2
    assert r._qa_path(seg.cache_key).exists()
    # cached + checked: no new work
    assert r.synthesize([seg]) == [] and takes == [0, 1]
