"""Review/edit page for one book: `./incant <book> --review` (stdlib HTTP server, no extra deps).

Everything the page changes is written to the same files the pipeline reads, in forms that survive
re-runs: speaker/emotion edits lock the span (attribution keeps locked spans), spoken-text edits go to
say_overrides.json (normalize applies them), voice choices become manual cast roles, character edits
lock the character, and pronunciations go to the book's lexicon.toml. Audio is never re-rendered here;
the page shows the command to run.
"""

from __future__ import annotations

import json
import re
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..config import Config
from ..schemas import NARRATOR_ID, AgeBracket, Cast, Emotion, Gender
from ..workdir import WorkDir, atomic_write

_KEY = re.compile(r"^[0-9a-f]{32}$")


class Book:
    """Read/modify one book's work directory on behalf of the page."""

    def __init__(self, cfg: Config, wd: WorkDir):
        self.cfg, self.wd = cfg, wd
        self._renderer = None
        self.voices = self.synth().voices()  # the configured backend's voices

    def synth(self):
        from ..pipeline import discover, instantiate

        stage = {s.name: s for s in discover()}["synthesize"]
        return instantiate(self.cfg, stage, self.cfg.pipeline.get("synthesize", "indextts"))

    # ------------------------------------------------------------ reads

    def renderer(self):
        """A Renderer for mapping spans to their cached audio segments (no TTS calls are made)."""
        if self._renderer is None:
            from ..stages.synthesize.render import Renderer

            method = self.synth()
            self._renderer = Renderer(self.cfg, self.wd, method, self.voices, self.wd.read_cast() or Cast(roles={}),
                                      strict_voices=False)
        self._renderer.cast = self.wd.read_cast() or Cast(roles={})
        return self._renderer

    def book(self) -> dict:
        wd = self.wd
        meta = wd.read_meta()
        chars = wd.read_characters()
        cast = wd.read_cast() or Cast(roles={})
        manifest = {c.chapter_id for c in wd.read_manifest().chapters} if wd.manifest_path.exists() else set()
        qa_path = wd.root / "audio" / "qa_report.json"
        voices = [v.model_dump(exclude_none=True) | {"has_sample": False} for v in self.voices.voices.values()]
        sampled = self._voice_samples()
        for v in voices:
            v["has_sample"] = v["id"] in sampled
        return {
            "meta": meta.model_dump(exclude_none=True),
            "chapters": [{"id": c.id, "title": c.title, "words": c.word_count, "rendered": c.id in manifest,
                          "has_spans": wd.spans_path(c.id).exists()} for c in wd.read_chapters().chapters],
            "characters": [c.model_dump(mode="json") for c in (chars.characters if chars else [])],
            "narrator_character": chars.narrator if chars else None,
            "cast": cast.model_dump(),
            "voices": sorted(voices, key=lambda v: (v.get("gender") is None, v["id"])),
            "qa": json.loads(qa_path.read_text()) if qa_path.exists() else [],
            "genders": [g.value for g in Gender], "ages": [a.value for a in AgeBracket],
        }

    def chapter(self, cid: str) -> dict:
        wd = self.wd
        ch = self._chapter(cid)
        spans = wd.read_spans(cid) if wd.spans_path(cid).exists() else []
        speech = wd.read_speech(cid)
        say = {s.id: s.say for s in speech.spans} if speech else {}
        overrides = self._overrides()
        seg_of: dict[str, dict] = {}
        try:
            for seg in self.renderer().plan(ch):
                path = self.renderer()._cache_path(seg.cache_key)
                for sid in seg.span_ids:
                    seg_of.setdefault(sid, {"key": seg.cache_key, "rendered": path.exists(), "text": seg.text,
                                            "voice": seg.voice})
        except Exception as e:  # noqa: BLE001 — the page still works without audio mapping
            seg_of = {"_error": {"message": str(e)}}
        paras: list[list[dict]] = []
        for s in spans:
            if not paras or paras[-1][0]["para"] != s.para:
                paras.append([])
            d = s.model_dump(mode="json", exclude_none=True)
            d["say"] = say.get(s.id)
            d["say_overridden"] = s.id in overrides and overrides[s.id].get("text") == s.text
            d["segment"] = seg_of.get(s.id)
            paras[-1].append(d)
        audio = wd.chapter_audio_dir / f"{cid}.wav"
        return {"chapter": ch.model_dump(), "paragraphs": paras, "audio": audio.exists(),
                "title_say": speech.title_say if speech else None}

    def lexicon(self) -> dict[str, str]:
        p = self.wd.root / "lexicon.toml"
        return {str(k): str(v) for k, v in tomllib.loads(p.read_text()).get("words", {}).items()} if p.exists() else {}

    # ------------------------------------------------------------ writes

    def save_span(self, body: dict) -> dict:
        sid = body["id"]
        cid = sid.split("-")[0]
        self._chapter(cid)
        spans = self.wd.read_spans(cid)
        span = next((s for s in spans if s.id == sid), None)
        if span is None:
            raise KeyError(f"no span {sid}")
        if "speaker" in body:
            sp = body["speaker"] or None
            if sp not in (None, NARRATOR_ID) and sp not in self._character_ids():
                raise ValueError(f"unknown character {sp}")
            span.speaker = sp
        if "emotion" in body:
            e = body["emotion"]
            span.emotion = Emotion(text=e["text"], alpha=float(e.get("alpha", 0.6))) if e and e.get("text") else None
        span.locked = True
        self.wd.write_spans(cid, spans)
        if "say" in body:
            ov = self._overrides()
            if body["say"] is None:
                ov.pop(sid, None)
            else:
                ov[sid] = {"text": span.text, "say": str(body["say"])}
            atomic_write(self.wd.root / "say_overrides.json", json.dumps(ov, indent=1, ensure_ascii=False) + "\n")
        return span.model_dump(mode="json", exclude_none=True)

    def save_cast(self, body: dict) -> dict:
        cast = self.wd.read_cast() or Cast(roles={})
        who, voice = body["character"], body.get("voice") or None
        if voice and voice not in self.voices.voices:
            raise ValueError(f"unknown voice {voice}")
        if voice:
            cast.roles[who] = voice
        else:
            cast.roles.pop(who, None)  # back to automatic casting on the next run
        cast.auto.pop(who, None)
        self.wd.write_cast(cast)
        return cast.model_dump()

    def save_character(self, body: dict) -> dict:
        chars = self.wd.read_characters()
        c = next((c for c in chars.characters if c.id == body["id"]), None) if chars else None
        if c is None:
            raise KeyError(f"no character {body['id']}")
        if "canonical_name" in body and body["canonical_name"].strip():
            c.canonical_name = body["canonical_name"].strip()
        if "gender" in body:
            c.gender = Gender(body["gender"])
        if "age" in body:
            c.age = AgeBracket(body["age"])
        c.locked = True
        self.wd.write_characters(chars)
        return c.model_dump(mode="json")

    def save_lexicon(self, words: dict[str, str]) -> dict[str, str]:
        clean = {k.strip(): v.strip() for k, v in words.items() if k.strip() and v.strip()}
        lines = ["# Pronunciations for this book (edited on the review page). Whole words, case-insensitive.", "[words]"]
        lines += [f"{json.dumps(k, ensure_ascii=False)} = {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(clean.items())]
        atomic_write(self.wd.root / "lexicon.toml", "\n".join(lines) + "\n")
        return clean

    # ------------------------------------------------------------ audio

    def segment_audio(self, key: str) -> Path | None:
        p = self.wd.cache_dir / key[:2] / f"{key}.wav"
        return p if _KEY.match(key) and p.exists() else None

    def chapter_audio(self, cid: str) -> Path | None:
        self._chapter(cid)
        p = self.wd.chapter_audio_dir / f"{cid}.wav"
        return p if p.exists() else None

    def voice_sample(self, voice: str) -> Path | None:
        key = self._voice_samples().get(voice)
        return self.segment_audio(key) if key else None

    # ------------------------------------------------------------ helpers

    def _voice_samples(self) -> dict[str, str]:
        """voice -> the longest-text clip already rendered in this book (no TTS needed to audition)."""
        best: dict[str, tuple[int, str]] = {}
        for f in self.wd.cache_dir.glob("*/*.qa.json"):
            rec = json.loads(f.read_text())
            v, n = rec.get("voice"), len(rec.get("text", ""))
            if v and n > best.get(v, (0, ""))[0]:
                best[v] = (n, f.name.removesuffix(".qa.json"))
        return {v: k for v, (_, k) in best.items()}

    def _overrides(self) -> dict:
        p = self.wd.root / "say_overrides.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _character_ids(self) -> set[str]:
        chars = self.wd.read_characters()
        return {c.id for c in chars.characters} if chars else set()

    def _chapter(self, cid: str):
        for c in self.wd.read_chapters().chapters:
            if c.id == cid:
                return c
        raise KeyError(f"no chapter {cid}")


def make_handler(book: Book):
    page = resources.files(__package__).joinpath("index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "incant-review"

        def log_message(self, fmt, *args):  # quiet: only errors
            if args and str(args[1])[:1] in "45":
                super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _file(self, path: Path | None):
            if path is None:
                return self._json({"error": "not found"}, 404)
            self._send(200, path.read_bytes(), "audio/wav")

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/api/book":
                    return self._json(book.book())
                if m := re.fullmatch(r"/api/chapter/(\d{4})", path):
                    return self._json(book.chapter(m.group(1)))
                if path == "/api/lexicon":
                    return self._json(book.lexicon())
                if m := re.fullmatch(r"/audio/segment/([0-9a-f]{32})\.wav", path):
                    return self._file(book.segment_audio(m.group(1)))
                if m := re.fullmatch(r"/audio/chapter/(\d{4})\.wav", path):
                    return self._file(book.chapter_audio(m.group(1)))
                if m := re.fullmatch(r"/audio/voice/([\w.-]+)\.wav", path):
                    return self._file(book.voice_sample(m.group(1)))
                self._json({"error": "not found"}, 404)
            except KeyError as e:
                self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                handlers = {"/api/span": book.save_span, "/api/cast": book.save_cast,
                            "/api/character": book.save_character, "/api/lexicon": book.save_lexicon}
                if path not in handlers:
                    return self._json({"error": "not found"}, 404)
                self._json(handlers[path](body))
            except (KeyError, ValueError, TypeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

    return Handler


def serve(cfg: Config, wd: WorkDir, host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(Book(cfg, wd)))
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    print(f"review page for '{wd.read_meta().title}': http://{shown}:{port}/   (Ctrl-C to stop)", flush=True)
    if host in ("0.0.0.0", "::"):
        print("  note: listening on all interfaces with no authentication — anyone on the network can edit", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
