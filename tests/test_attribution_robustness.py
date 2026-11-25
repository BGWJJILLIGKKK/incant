import json
from unittest.mock import MagicMock


from incant.config import Config, PathsConfig
from incant.llm import LLMError
from incant.pipeline import Step, discover, instantiate, run_pipeline
from incant.schemas import Character, CharacterList, Chapter, ChapterList
from incant.stages.attribute.speakers.speakers import SpeakersAttribute, SpeakersSettings
from incant.stages.attribute.spans import split_chapter
from incant.stages.base import Context
from incant.workdir import WorkDir


def test_speaker_requests_include_following_evidence_and_split_duplicate_ids():
    method = SpeakersAttribute(SpeakersSettings(), Config())
    target = [split_chapter("0001", '“Wait,” said Alice.'), split_chapter("0002", '“Go,” said Alice.')]
    following = [split_chapter("0003", 'Alice added that the gate had shut.')]
    seen = []

    class Model:
        def chat_json(self, system, prompt, schema, **kwargs):
            seen.append(prompt)
            assert "[context] Alice added that the gate had shut." in prompt
            count = prompt.count("⟦")
            return schema(quotes=[
                {"id": 1, "cue": "tag", "speaker": "alice", "other_role": "", "other_gender": "unknown",
                 "emotion": "neutral", "intensity": 0.0}
            ] * count)

    roster = [Character(id="alice", canonical_name="Alice", speaks=True)]
    result = method._label_with_retry(Model(), "0001", [], target, roster, following=following)
    assert set(result) == {"0001-00001", "0002-00001"}
    assert len(seen) == 3


def test_narrator_prompt_can_include_less_mentioned_candidate(tmp_path):
    wd = WorkDir(tmp_path)
    chapter = Chapter(id="0001", index=1, title="Chapter 1", file="one.txt", word_count=1000)
    wd.write_chapters(ChapterList(method="headings", chapters=[chapter]))
    (tmp_path / chapter.file).write_text("I looked at my reflection. " * 300 + "\n\nMy name was Alice.")
    roster = CharacterList(characters=[
        *[Character(id=f"other-{i}", canonical_name=f"Other {i}", mentions=100) for i in range(20)],
        Character(id="alice", canonical_name="Alice", mentions=1),
    ])
    cfg = Config()

    class Model:
        def chat_json(self, system, prompt, schema, **kwargs):
            assert "My name was Alice." in prompt and "alice: Alice" in prompt
            return schema(narrator="alice", reason="The narrator states her name.")

    assert SpeakersAttribute(SpeakersSettings(), cfg)._find_narrator(Model(), wd, roster, Context(cfg=cfg)) == "alice"


def test_speakers_retries_and_writes_errors_json(tmp_path):
    cfg = Config(paths=PathsConfig(work_dir=tmp_path))
    stage = {s.name: s for s in discover()}["attribute"]
    method: SpeakersAttribute = instantiate(cfg, stage, "speakers")

    wd = WorkDir(tmp_path / "book")
    wd.root.mkdir(parents=True, exist_ok=True)
    wd.write_chapters(ChapterList(method="headings", chapters=[Chapter(id="0001", index=1, title="Ch 1", file="0001.md", word_count=50)]))
    wd.write_characters(CharacterList(characters=[Character(id="alice", canonical_name="Alice", speaks=True)]))
    (wd.root / "0001.md").write_text('"Hello," said Alice.\n')

    ctx = MagicMock()
    ctx.need_wd.return_value = wd
    ctx.chapters = None
    ctx.cfg = cfg
    ctx.log = MagicMock()

    calls = []

    def fail_label(*args, **kwargs):
        calls.append(1)
        raise LLMError("API rate limit exceeded")

    method._find_narrator = MagicMock(return_value=None)
    method._label = fail_label

    method.run(ctx)
    # 1 initial + 2 retries = 3 calls
    assert len(calls) == 3
    errors_file = wd.root / "attribution_errors.json"
    assert errors_file.exists()
    errs = json.loads(errors_file.read_text())
    assert len(errs) == 1
    assert errs[0]["chapter"] == "0001"
    assert "API rate limit exceeded" in errs[0]["error"]


def test_pipeline_marks_incomplete_on_attribution_errors(tmp_path, monkeypatch):
    cfg = Config(paths=PathsConfig(work_dir=tmp_path))
    stage = {s.name: s for s in discover()}["attribute"]
    wd = WorkDir(tmp_path / "book")
    wd.root.mkdir(parents=True, exist_ok=True)
    wd.write_chapters(ChapterList(method="headings", chapters=[Chapter(id="0001", index=1, title="Ch 1", file="0001.md", word_count=50)]))
    wd.write_characters(CharacterList(characters=[Character(id="alice", canonical_name="Alice", speaks=True)]))
    (wd.root / "0001.md").write_text('"Hello," said Alice.\n')

    ctx = Context(cfg=cfg, wd=wd)
    step = Step(stage=stage, method="speakers", why="cli")

    # Mock SpeakersAttribute.run to write attribution_errors.json
    def fake_run(self, ctx):
        (ctx.need_wd().root / "attribution_errors.json").write_text('[{"error": "fail"}]')
        ctx.need_wd().write_spans("0001", [])

    monkeypatch.setattr(SpeakersAttribute, "run", fake_run)

    run_pipeline([step], ctx)

    rec = json.loads((wd.root / "pipeline.json").read_text())
    assert rec["attribute"]["incomplete"] is True

    # Running again with incomplete flag should re-execute, NOT be marked up to date
    executed = []
    def fake_run_2(self, ctx):
        executed.append(1)
        # Clear errors
        (ctx.need_wd().root / "attribution_errors.json").unlink()
        ctx.need_wd().write_spans("0001", [])

    monkeypatch.setattr(SpeakersAttribute, "run", fake_run_2)
    run_pipeline([step], ctx)
    assert len(executed) == 1
    rec_after = json.loads((wd.root / "pipeline.json").read_text())
    assert not rec_after["attribute"].get("incomplete")
