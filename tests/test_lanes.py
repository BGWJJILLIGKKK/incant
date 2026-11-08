"""Batch lanes: one book at a time per server, books in parallel across servers; and batch --from."""

import threading
import time
from types import SimpleNamespace

import pytest

from incant.cli import main
from incant.config import LLMConfig
from incant.lanes import Job, Phase, endpoint, lane_key, llm_endpoint, plan_phases, run_lanes
from incant.stages.synthesize.dummy.dummy import DummyTTS


@pytest.mark.parametrize("url, want", [
    ("http://localhost:8080/v1", "localhost:8080"),
    ("http://127.0.0.1:8080", "localhost:8080"),
    ("https://api.elevenlabs.io", "api.elevenlabs.io:443"),
    ("http://Host.Docker.Internal:11434/v1", "host.docker.internal:11434"),
])
def test_endpoint(url, want):
    assert endpoint(url) == want


def test_llm_endpoint_for_claude_ignores_the_local_default():
    assert llm_endpoint(LLMConfig(dialect="anthropic")) == "api.anthropic.com:443"
    assert llm_endpoint(LLMConfig()) == "localhost:8080"


def test_lane_modes():
    assert lane_key("localhost:8080", "host") == lane_key("localhost:8880", "host") == "localhost"
    assert lane_key("localhost:8080", "endpoint") != lane_key("localhost:8880", "endpoint")
    assert lane_key("kokoro", "host") == "kokoro" and lane_key(None, "host") is None
    assert lane_key("localhost:8080", "off") == lane_key("api.anthropic.com:443", "off")


def steps(*pairs):
    return [SimpleNamespace(stage=SimpleNamespace(name=n), method=m) for n, m in pairs]


RES = {"txt": "ingest", "characters": "llm:1", "speakers": "llm:1", "indextts": "tts:2", "kokoro": "kokoro"}
PIPE = steps(("ingest", "txt"), ("parse", "characters"), ("attribute", "speakers"), ("normalize", "rules"),
             ("synthesize", "indextts"), ("output", "m4b"))


def test_phases_split_where_the_server_changes():
    ph = plan_phases(PIPE, lambda s: RES.get(s.method), "endpoint")
    assert [(p.lane, p.stages) for p in ph] == [
        ("ingest", ["ingest"]), ("llm:1", ["parse", "attribute", "normalize"]), ("tts:2", ["synthesize", "output"])]


def test_one_server_for_llm_and_tts_is_one_lane():
    """A llama-swap serving both models must not be asked to swap between books."""
    res = dict(RES, indextts="llm:1")
    ph = plan_phases(PIPE, lambda s: res.get(s.method), "endpoint")
    assert [p.lane for p in ph] == ["ingest", "llm:1"]


def test_phases_respect_from_and_until():
    ph = plan_phases(PIPE, lambda s: RES.get(s.method), "endpoint", start="normalize", stop="synthesize")
    assert [(p.lane, p.stages) for p in ph] == [("tts:2", ["normalize", "synthesize"])]


def test_lanes_overlap_books_but_never_two_in_one_lane():
    active: dict[str, int] = {}
    peak: dict[str, int] = {}
    most = [0]
    lock = threading.Lock()
    order: list[tuple[int, str]] = []

    def make(n):
        def run(phase: Phase, i: int):
            with lock:
                active[phase.lane] = active.get(phase.lane, 0) + 1
                peak[phase.lane] = max(peak.get(phase.lane, 0), active[phase.lane])
                most[0] = max(most[0], sum(active.values()))
                order.append((n, phase.lane))
            time.sleep(0.05)
            with lock:
                active[phase.lane] -= 1
        return run

    jobs = [Job(key=n, phases=[Phase("a", ["ingest"]), Phase("b", ["synthesize"])], run=make(n)) for n in range(4)]
    finished = []
    run_lanes(jobs, lambda j: finished.append(j.key))
    assert all(j.done and j.error is None for j in jobs) and sorted(finished) == [0, 1, 2, 3]
    assert peak == {"a": 1, "b": 1} and most[0] == 2
    assert [n for n, lane in order if lane == "b"] == [0, 1, 2, 3]  # books keep their order in every lane


def test_a_failed_book_skips_its_later_phases_and_the_batch_goes_on():
    ran = []

    def run(phase, i, n):
        ran.append((n, phase.lane))
        if n == 0 and phase.lane == "a":
            raise RuntimeError("bad page")

    jobs = [Job(key=n, phases=[Phase("a", ["ingest"]), Phase("b", ["output"])],
                run=lambda p, i, n=n: run(p, i, n)) for n in range(2)]
    run_lanes(jobs)
    assert isinstance(jobs[0].error, RuntimeError) and not jobs[0].done and jobs[1].done
    assert (0, "b") not in ran and (1, "b") in ran


# ------------------------------------------------------------------ the CLI

TEXT = 'Chapter 1\n\n"Hello," said {0}. "It is a fine day."\n\nChapter 2\n\n{0} walked home.\n'


@pytest.fixture
def books(tmp_path, monkeypatch):
    monkeypatch.delenv("INCANT_DATA", raising=False)
    d = tmp_path / "in"
    d.mkdir()
    for name in ("Ann", "Bob", "Cy"):
        (d / f"{name}.txt").write_text(TEXT.format(name))
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\noutput_dir = "out"\n')
    return tmp_path, d, conf


@pytest.mark.parametrize("lanes", ["host", "off"])
def test_batch_in_lanes(books, monkeypatch, capsys, lanes):
    tmp, d, conf = books
    conf.write_text(conf.read_text() + f'[run]\nlanes = "{lanes}"\n')
    monkeypatch.setattr(DummyTTS, "resource", lambda self: "localhost:9")  # as if it were a speech server
    assert main([str(d), "basic", "tags", "rules", "dummy", "chapters", "-c", str(conf)]) == 0
    out = capsys.readouterr().out
    assert "batch finished: 3 ok, 0 failed" in out
    assert ("ingest (ingest, parse, attribute, normalize) → localhost (synthesize, output)" in out) == (lanes == "host")
    assert ("[2] [synthesize] dummy" in out) == (lanes == "host")  # lines carry their book when books overlap
    assert out.index("ok    Ann.txt") < out.index("ok    Bob.txt") < out.index("ok    Cy.txt")
    assert len(list((tmp / "out").rglob("*.mp3"))) == 6


def test_batch_from_finds_each_book(books, capsys):
    tmp, d, conf = books
    assert main([str(d), "basic", "tags", "-c", str(conf), "--until", "attribute"]) == 0
    (d / "Dee.txt").write_text(TEXT.format("Dee"))  # never ingested
    capsys.readouterr()
    rc = main([str(d), "basic", "tags", "rules", "dummy", "chapters", "-c", str(conf), "--from", "normalize"])
    out = capsys.readouterr()
    assert rc == 1 and "batch finished: 3 ok, 1 failed" in out.out
    assert "Dee.txt" in out.err and "hasn't been ingested yet" in out.err
    assert not (tmp / "work" / "dee").exists()
