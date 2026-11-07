"""Generic pipeline pieces: per-backend voices and casts, HTTP speech, Kokoro, LLM dialects, output formats."""

import json
import shutil
import subprocess
import wave
from pathlib import Path

import httpx
import pytest

from incant.cli import main
from incant.config import Config, LLMConfig, load_config
from incant.llm import LLM
from incant.pipeline import discover, instantiate, resolve
from incant.schemas import NARRATOR_ID, Cast, CharacterList
from incant.workdir import WorkDir

STAGES = {s.name: s for s in discover()}
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def cfg_in(tmp_path, extra=""):
    (tmp_path / "voices").mkdir(exist_ok=True)
    (tmp_path / "voices" / "voices.json").write_text(json.dumps(
        {"anna": {"gender": "female"}, "bert": {"gender": "male"}}))
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\nvoices_dir = "voices"\n[cast]\nnarrator = "anna"\n' + extra)
    return load_config(conf)


def synth(cfg, name):
    return instantiate(cfg, STAGES["synthesize"], name)


# ------------------------------------------------------------ voices and casts


def test_each_backend_has_its_own_voices_and_narrator(tmp_path):
    cfg = cfg_in(tmp_path)
    idx, kok = synth(cfg, "indextts"), synth(cfg, "kokoro")
    assert set(idx.voices().voices) == {"anna", "bert"} and idx.narrator() == "anna"
    lib = kok.voices().voices
    assert len(lib) == 54 and lib["bf_emma"].lang == "en-gb" and lib["am_michael"].gender == "male"
    assert lib["af_nicole"].gender is None and lib["jf_alpha"].gender is None  # whispery / non-English: hand-cast only
    assert kok.narrator() == "af_heart"  # [cast] narrator "anna" isn't a Kokoro voice
    (tmp_path / "voices" / "kokoro.json").write_text(json.dumps(
        {"af_sky": {"age": "teen"}, "warm": {"mix": {"af_heart": 0.7, "af_bella": 0.3}, "gender": "female"}}))
    kok = synth(cfg, "kokoro")
    lib = kok.voices().voices
    assert lib["af_sky"].age == "teen" and lib["af_sky"].lang == "en-us" and lib["warm"].mix["af_heart"] == 0.7
    assert "warm" not in idx.voices().voices  # kokoro.json isn't read as an IndexTTS voice file
    assert all(ok for ok, msg in kok.check() if "blend" in msg)


def test_switching_backends_keeps_each_cast(tmp_path):
    cfg = cfg_in(tmp_path)
    wd = WorkDir(tmp_path / "work" / "b")
    wd.root.mkdir(parents=True)
    wd.write_cast(Cast(roles={NARRATOR_ID: "anna", "ann": "bert"}))  # older cast.json: no engine
    idx, kok = synth(cfg, "indextts"), synth(cfg, "kokoro")
    assert idx.load_cast(wd).engine == "indextts"  # adopted: all its voices are IndexTTS voices
    wd.write_cast(Cast(roles={NARRATOR_ID: "anna", "ann": "bert"}, engine="indextts"))
    k = kok.load_cast(wd)
    assert k.engine == "kokoro" and k.roles == {}
    assert json.loads(wd.cast_path_for("indextts").read_text())["roles"]["ann"] == "bert"
    k.roles["ann"] = "bf_emma"
    wd.write_cast(k)
    back = idx.load_cast(wd)
    assert back.roles["ann"] == "bert" and back.engine == "indextts"
    wd.write_cast(back)
    assert kok.load_cast(wd).roles["ann"] == "bf_emma"  # hand edits survive the round trip


# ------------------------------------------------------------ HTTP speech


def mock_http(method, seen):
    def handler(req):
        seen.append((req.headers.get("authorization"), json.loads(req.content)))
        return httpx.Response(200, content=b"RIFFfake")
    method.http = httpx.Client(base_url="http://tts.test/v1", transport=httpx.MockTransport(handler),
                               headers=dict(method.http.headers))


def test_openai_speech_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_KEY", "sk-test")
    cfg = cfg_in(tmp_path, '[synthesize.openai]\napi_key_env = "TTS_KEY"\ninstructions = true\n')
    (tmp_path / "voices" / "openai.json").write_text(json.dumps({"warm": {"mix": {"af_heart": 0.7, "af_bella": 0.3}}}))
    m = synth(cfg, "openai")
    seen = []
    mock_http(m, seen)
    from incant.schemas import Emotion
    lib = m.voices().voices
    m.synthesize("Hello.", lib["warm"], Emotion(text="quiet grief", alpha=0.5))
    m.synthesize("Again.", lib["af_heart"], None, variant=1)
    (auth, a), (_, b) = seen
    assert auth == "Bearer sk-test" and a["voice"] == "af_heart(0.7)+af_bella(0.3)" and "quiet grief" in a["instructions"]
    assert b["voice"] == "af_heart" and b["speed"] == 0.96 and "instructions" not in b
    assert m.voice_space() == "openai-kokoro" and m.narrator() == "af_heart"


def test_indextts_requests_unchanged(tmp_path):
    """Its cache keys depend on identity(); its audio on this body. Both must stay as they were."""
    m = synth(cfg_in(tmp_path), "indextts")
    assert m.identity() == "indextts-http:indextts-2.5:en:speed=1.0:random=False:p2"
    from incant.schemas import Emotion, Voice
    seen = []
    mock_http(m, seen)
    m.synthesize("Hi.", Voice(id="anna"), Emotion(text="wry", alpha=0.4))
    m.synthesize("Hi.", Voice(id="anna"), None, variant=1)
    assert seen[0][1] == {"model": "indextts-2.5", "input": "Hi.", "voice": "anna", "response_format": "wav",
                          "extra_params": {"lang": "en", "use_random": False, "use_emo_text": True,
                                           "emo_text": "wry", "emo_alpha": 0.4}}
    assert seen[1][1]["extra_params"]["use_random"] is True


# ------------------------------------------------------------ LLM dialects


def test_llm_bodies(monkeypatch):
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int

    legacy = {  # what every earlier version sent: cached answers are keyed by it
        "model": "qwen3.5-9b", "temperature": 0.0, "max_tokens": 100,
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "Out", "schema": Out.model_json_schema(), "strict": True}},
        "chat_template_kwargs": {"enable_thinking": True},
    } | LLMConfig().thinking_sampling
    assert LLM(LLMConfig()).body("s", "u", Out, True, 100) == legacy
    o = LLM(LLMConfig(dialect="openai", structured="json_object")).body("s", "u", Out, True, 100)
    assert "chat_template_kwargs" not in o and "top_k" not in o and o["response_format"] == {"type": "json_object"}
    assert '"x"' in o["messages"][0]["content"]  # the schema goes into the prompt
    monkeypatch.setenv("LLM_KEY", "k1")
    assert LLM(LLMConfig(api_key_env="LLM_KEY")).http.headers["authorization"] == "Bearer k1"


def test_llm_json_fences():
    from incant.llm import _strip_fences

    assert _strip_fences('```json\n{"x": 1}\n```') == '{"x": 1}' and _strip_fences(' {"x": 1} ') == '{"x": 1}'


# ------------------------------------------------------------ resolution


def test_output_method_from_extension_and_stage_defaults(tmp_path):
    src = tmp_path / "b.txt"
    src.write_text("Title: B\n\nChapter 1\n\nHi.\n")
    steps, out = resolve(["dummy", str(tmp_path / "x.mp3")], str(src), Config(), list(STAGES.values()))
    plan = {s.stage.name: (s.method, s.why) for s in steps}
    assert plan["output"] == ("mp3", "cli") and plan["parse"] == ("basic", "default")
    steps, _ = resolve([], str(src), Config(), list(STAGES.values()))
    assert {s.stage.name: s.method for s in steps}["synthesize"] == "kokoro"


# ------------------------------------------------------------ rule-based attribution


CHAPTER = """Chapter 1

Mr. Hale came into the room. "Good morning," said Mr. Hale.

"Good morning, father," replied Margaret. She smiled at him.

"Did you sleep?" he asked.

"Not at all."

"I am sorry to hear it."

Margaret looked out of the window. "It is raining again."

"Then we shall stay in," said she, "and read."

"Hush!" whispered Mr. Hale.
"""


def test_tags_attribution_without_characters(tmp_path, capsys):
    src = tmp_path / "book.txt"
    src.write_text("Title: Rain\n\n" + CHAPTER)
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n')
    assert main([str(src), "basic", "tags", "--until", "attribute", "-c", str(conf)]) == 0
    wd = WorkDir(tmp_path / "work" / "rain")
    chars = CharacterList.model_validate_json(wd.characters_path.read_text())
    by = {c.canonical_name: c for c in chars.characters}
    assert by["Mr. Hale"].gender.value == "male" and by["Margaret"].gender.value == "female"
    ch = wd.read_chapters().chapters[0].id
    said = [s.speaker for s in wd.read_spans(ch) if s.kind == "dialogue"]
    ids = {c.canonical_name: c.id for c in chars.characters}
    hale, meg = ids["Mr. Hale"], ids["Margaret"]
    # tag, tag, pronoun tag, turn-taking x2, action beat, pronoun tag (twice: same paragraph), tag
    assert said == [hale, meg, hale, meg, hale, meg, meg, meg, hale]
    hush = [s for s in wd.read_spans(ch) if s.text.startswith('"Hush')][0]
    assert hush.emotion and "whisper" in hush.emotion.text
    assert "quotations attributed" in capsys.readouterr().out


# ------------------------------------------------------------ Kokoro end to end


def kokoro_ready():
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    d = Path("~/.cache/incant/kokoro").expanduser()
    return (d / "kokoro-v1.0.onnx").exists() and (d / "voices-v1.0.bin").exists()


@pytest.mark.skipif(not (HAVE_FFMPEG and kokoro_ready()), reason="needs ffmpeg, kokoro-onnx and its model files")
def test_kokoro_book_to_mp3_and_chapter_files(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("Title: Rain\nAuthor: A. Writer\n\n" + CHAPTER + "\nChapter 2\n\nThe end.\n")
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\noutput_dir = "out"\n[synthesize.kokoro]\nqa = false\n'
                    '[output.chapters]\nformat = "opus"\nbitrate = "32k"\n')
    assert main([str(src), "basic", "tags", "kokoro", "mp3", "-c", str(conf)]) == 0
    mp3 = tmp_path / "out" / "A. Writer" / "Rain" / "Rain.mp3"
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(mp3)],
                                      capture_output=True, check=True).stdout)
    assert len(probe["chapters"]) == 2 and probe["format"]["tags"]["artist"] == "A. Writer"
    assert float(probe["format"]["duration"]) > 5
    cast = json.loads((tmp_path / "work" / "rain" / "cast.json").read_text())
    assert cast["engine"] == "kokoro" and cast["roles"][NARRATOR_ID] == "af_heart"
    assert {cast["roles"][c] for c in cast["roles"] if c != NARRATOR_ID} <= set(synth(load_config(conf), "kokoro").voices().voices)

    assert main(["rain", "chapters", "--from", "output", "-c", str(conf)]) == 0
    files = sorted(p.name for p in (tmp_path / "out" / "A. Writer" / "Rain").iterdir())
    assert files == ["01 - Chapter 1.opus", "02 - Chapter 2.opus", "Rain.m3u", "Rain.mp3"]
    with wave.open(str(tmp_path / "work" / "rain" / "audio" / "chapters" / "0001.wav")) as w:
        assert w.getnframes() / w.getframerate() > 3


def test_check_on_a_batch_only_checks(tmp_path, capsys):
    books = tmp_path / "books"
    books.mkdir()
    (books / "a.txt").write_text("Title: A\n\nChapter 1\n\nHi.\n")
    conf = tmp_path / "incant.toml"
    conf.write_text('[paths]\nwork_dir = "work"\n')
    main([str(books), "dummy", "-c", str(conf), "--check"])
    assert "ok  ffmpeg" in capsys.readouterr().out or not HAVE_FFMPEG
    assert not (tmp_path / "work").exists()  # nothing ingested


def test_data_dir_holds_books_and_user_settings(tmp_path, monkeypatch):
    app, data = tmp_path / "app", tmp_path / "data"
    app.mkdir(), data.mkdir()
    (app / "incant.toml").write_text('[paths]\nvoices_dir = "voices"\n[llm]\nmodel = "m1"\n')
    (data / "incant.local.toml").write_text('[llm]\nmodel = "m2"\n')
    monkeypatch.setenv("INCANT_DATA", str(data))
    cfg = load_config(app / "incant.toml")
    assert cfg.paths.work_dir == data / "work" and cfg.paths.output_dir == data / "output"
    assert cfg.paths.voices_dir == app / "voices"  # the program's voice list stays with the program
    assert cfg.llm.model == "m2"  # the data directory's settings are read last


def test_env_file_names_the_data_dir_and_paths_resolve_there(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("INCANT_DATA", raising=False)
    app, data = tmp_path / "app", tmp_path / "data"
    (data / "books").mkdir(parents=True)
    app.mkdir()
    (app / "incant.toml").write_text('[pipeline]\nparse = "basic"\n')
    (app / ".env").write_text(f"# personal\nINCANT_DATA='{data}'\nUID=1000\n")
    (data / "incant.local.toml").write_text('[ingest.url.sites."ex.test"]\ncookies = "cookies.txt"\n')
    (data / "cookies.txt").write_text("# Netscape HTTP Cookie File\n.ex.test\tTRUE\t/\tTRUE\t0\ts\tv\n")
    (data / "books" / "b.txt").write_text("Title: B\n\nChapter 1\n\nHi.\n")
    cfg = load_config(app / "incant.toml")
    assert cfg.paths.work_dir == data / "work"
    url = instantiate(cfg, {s.name: s for s in discover()}["ingest"], "url")
    assert url.check() == [(True, f"ex.test: cookies file {data / 'cookies.txt'}")]  # relative to the data dir
    monkeypatch.chdir(tmp_path)  # books/b.txt isn't here: it's found in the data directory
    assert main(["books/b.txt", "--until", "parse", "-c", str(app / "incant.toml")]) == 0
    assert (data / "work" / "b" / "chapters.json").exists()
