import json

from incant.config import Config, PathsConfig
from incant.pipeline import discover, instantiate
from incant.schemas import Cast, Chapter, ChapterAudio, ChapterList, Segment
from incant.stages.synthesize.render import Renderer
from incant.workdir import WorkDir, atomic_write


def make_renderer(tmp_path):
    cfg = Config(paths=PathsConfig(work_dir=tmp_path))
    stage = {s.name: s for s in discover()}["synthesize"]
    synth = instantiate(cfg, stage, "dummy")

    wd = WorkDir(tmp_path / "book")
    wd.root.mkdir(parents=True, exist_ok=True)
    return Renderer(cfg, wd, synth, synth.voices(), Cast(roles={"narrator": "voice"}), strict_voices=False)


def segment(key):
    return Segment(span_ids=[], text=key, voice="voice", cache_key=key, pause_after_ms=0)


def test_gain_baseline_persistence(tmp_path):
    renderer = make_renderer(tmp_path)
    clip = segment("001122")
    level = renderer._lvl_path(clip.cache_key)
    atomic_write(level, json.dumps({"lufs": -20.0}))
    assert renderer.voice_gains([clip]) == {"voice": -1.0}

    atomic_write(level, json.dumps({"lufs": -26.0}))
    assert renderer.voice_gains([clip], partial=True) == {"voice": -1.0}
    renderer.cast.roles["alice"] = "alternate"
    assert renderer.voice_gains([clip], partial=True) == {"voice": 5.0}


def test_full_render_uses_same_gains_excluding_stale_clips(tmp_path, monkeypatch):
    renderer = make_renderer(tmp_path)
    chapters = [Chapter(id=f"{n:04d}", index=n, title=str(n), file=f"{n}.txt", word_count=100) for n in (1, 2)]
    renderer.wd.write_chapters(ChapterList(method="headings", chapters=chapters))
    clips = {ch.id: [segment(ch.id)] for ch in chapters}
    levels = {"0001": -29.0, "0002": -19.0}
    atomic_write(renderer._lvl_path("stale"), json.dumps({"lufs": -45.0}))
    assembled = []

    def measure(segs):
        for clip in segs:
            atomic_write(renderer._lvl_path(clip.cache_key), json.dumps({"lufs": levels[clip.cache_key]}))

    def assemble(ch, segs, gains):
        assembled.append((ch.id, gains.copy()))
        return ChapterAudio(chapter_id=ch.id, title=ch.title, file=ch.file, duration_ms=1, segments=len(segs))

    monkeypatch.setattr(renderer, "plan", lambda ch: clips[ch.id])
    monkeypatch.setattr(renderer, "synthesize", lambda *args: [])
    monkeypatch.setattr(renderer, "measure", measure)
    monkeypatch.setattr(renderer, "assemble", assemble)
    renderer.render()
    assert assembled == [("0001", {"voice": 3.0}), ("0002", {"voice": 3.0})]
