from incant.qa import Check, edit_distance, words


def test_words_normalise_case_punctuation_hyphens():
    assert words("“Good-humoured,” said Mr. Bennet’s wife.") == ["good", "humoured", "said", "mr", "bennets", "wife"]


def test_numbers_spelled_out():
    from incant.qa import compare

    assert words("10,000 a year") == ["ten", "thousand", "a", "year"]
    assert compare("the two third he danced", "the 2-3rd he danced").edits == 0


def test_contractions_and_initials_compare_equal():
    from incant.qa import compare

    assert compare("What’s today!", "What is today?").edits == 0
    assert compare("Thank’ee,", "Thank you.").edits == 0
    assert compare("C. D.", "CD.").edits == 0


def test_edit_distance_and_short_line_tolerance():
    assert edit_distance("a b c d".split(), "a x c".split()) == 2
    assert Check(wer=1.0, edits=1, heard="").ok(0.25)          # one-word line misheard: whisper noise
    assert not Check(wer=0.5, edits=3, heard="").ok(0.25)


def test_whitelist_proper_nouns_discounted():
    from incant.qa import compare

    # "fezziwig" misheard as "fuzzywig"
    chk_no_wl = compare("mr fezziwig laughed", "mr fuzzywig laughed")
    assert chk_no_wl.edits == 1
    chk_wl = compare("mr fezziwig laughed", "mr fuzzywig laughed", whitelist={"fezziwig"})
    assert chk_wl.edits == 0
    assert chk_wl.wer == 0.0


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


def test_transcript_normalised_like_the_text():
    from incant.qa import compare
    from incant.speech import normalize

    said = normalize("“To-morrow,” said Mr. Bennet, “in 1888.”")
    assert compare(said, "Tomorrow, said Mr. Bennet, in 1888.").edits == 0


def test_apply_gain_scales_and_never_clips():
    import numpy as np

    from incant.stages.synthesize.render import apply_gain

    x = (np.sin(np.linspace(0, 100, 2205)) * 1000).astype(np.int16)
    up6 = np.frombuffer(apply_gain(x.tobytes(), 6.0), dtype=np.int16)
    assert abs(int(np.abs(up6).max()) / int(np.abs(x).max()) - 10 ** (6 / 20)) < 0.01
    loud = np.frombuffer(apply_gain((x * 30).astype(np.int16).tobytes(), 12.0), dtype=np.int16)
    assert np.abs(loud.astype(np.int32)).max() <= 32767 * 10 ** (-1 / 20) + 1
