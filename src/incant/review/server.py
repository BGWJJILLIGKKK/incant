"""Review/edit page for one book: `./incant <book> --review` (stdlib HTTP server, no extra deps).

Everything the page changes is written to the same files the pipeline reads, in forms that survive
re-runs: speaker/emotion edits lock the span (attribution keeps locked spans), spoken-text edits go to
say_overrides.json (normalize applies them), voice choices become manual cast roles, character edits
lock the character, and pronunciations go to the book's lexicon.toml. Auditions and chapter renders
use the current edits.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections import Counter
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from ipaddress import ip_address
from pathlib import Path
from threading import Lock
from urllib.parse import unquote, urlparse, urlsplit

from ..config import Config
from ..pipeline import discover, instantiate, read_records
from ..schemas import NARRATOR_ID, AgeBracket, AudioManifest, Cast, Emotion, Gender, SpanKind
from ..stages.normalize.base import override_matches, read_say_overrides
from ..workdir import WorkDir, atomic_write

_KEY = re.compile(r"^[0-9a-f]{32}$")


class Book:
    """Read/modify one book's work directory on behalf of the page."""

    def __init__(self, cfg: Config, wd: WorkDir):
        self.cfg, self.wd = cfg, wd
        self.write_lock = Lock()
        self.stages = {s.name: s for s in discover()}
        self.records = read_records(wd)
        self.normalizer = self._method("normalize")
        self._synth = self._method("synthesize")
        self.voices = self.synth().voices()

    def synth(self):
        return self._synth

    def _method(self, name):
        stage = self.stages[name]
        recorded = self.records.get(name, {}).get("method")
        method = recorded if recorded in stage.methods else self.cfg.pipeline.get(name) or stage.module.DEFAULT
        return instantiate(self.cfg, stage, method)

    def _cast(self):
        method = self.synth()
        space = method.voice_space()
        cast = self.wd.read_cast()
        path = self.wd.cast_path
        if space is not None and cast is not None and not method.matches_cast(cast):
            path = self.wd.cast_path_for(space)
            cast = self.wd.read_cast(path)
        return cast or Cast(roles={}, engine=space), path

    def _speech(self, ch):
        if not self.wd.spans_path(ch.id).exists():
            raise ValueError(f"chapter {ch.id} has no spans; run attribution first")
        method = self.normalizer
        return method.chapter_speech(self.wd, ch, method.lexicon(self.wd), self._overrides())

    # ------------------------------------------------------------ reads

    def renderer(self, *, prepare: bool = False):
        """A Renderer for mapping spans to their cached audio segments (no TTS calls are made)."""
        from ..stages.synthesize.render import Renderer

        method = self.synth()
        cast, path = self._cast()
        if prepare:
            cast = method.prepare_cast(self.wd, cast)
            self.wd.write_cast(cast, path)
        return Renderer(self.cfg, self.wd, method, self.voices, cast, strict_voices=prepare and method.NEEDS_VOICE_LIBRARY)

    def book(self) -> dict:
        wd = self.wd
        meta = wd.read_meta()
        chars = wd.read_characters()
        cast, _ = self._cast()
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
            "synthesize": self.synth().name,
            "normalize": self.normalizer.name,
            "voices": sorted(voices, key=lambda v: (v.get("gender") is None, v["id"])),
            "qa": json.loads(qa_path.read_text()) if qa_path.exists() else [],
            "genders": [g.value for g in Gender], "ages": [a.value for a in AgeBracket],
        }

    def chapter(self, cid: str) -> dict:
        wd = self.wd
        ch = self._chapter(cid)
        speech = self._speech(ch) if wd.spans_path(cid).exists() else None
        spans = speech.spans if speech else []
        overrides = self._overrides()
        seg_of: dict[str, dict] = {}
        try:
            renderer = self.renderer()
            for seg in renderer.plan(ch, speech=speech) if speech else []:
                path = renderer._cache_path(seg.cache_key)
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
            override = overrides.get(s.id, {})
            d["say_overridden"] = "say" in override and override_matches(override, s.text)
            d["segment"] = None if s.omitted else seg_of.get(s.id) or seg_of.get("_error")
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
        if "omit" in body and not isinstance(body["omit"], bool):
            raise ValueError("omit must be true or false")
        if "speaker" in body:
            sp = body["speaker"] or None
            if sp not in (None, NARRATOR_ID) and sp not in self._character_ids():
                raise ValueError(f"unknown character {sp}")
            span.speaker = sp
        if "emotion" in body:
            e = body["emotion"]
            span.emotion = Emotion(text=e["text"], alpha=float(e.get("alpha", 0.6))) if e and e.get("text") else None
        if "omit" not in body or {"say", "speaker", "emotion"} & body.keys():
            span.locked = True
            self.wd.write_spans(cid, spans)
        if "say" in body or "omit" in body:
            ov = self._overrides()
            current = ov.get(sid, {})
            entry = (current | {"text": span.text}) if override_matches(current, span.text) else {"text": span.text}
            if "say" in body:
                if body["say"] is None:
                    entry.pop("say", None)
                else:
                    entry["say"] = str(body["say"])
            if body.get("omit"):
                entry["omit"] = True
            elif "omit" in body:
                entry.pop("omit", None)
            if "say" not in entry and "omit" not in entry:
                ov.pop(sid, None)
            else:
                ov[sid] = entry
            atomic_write(self.wd.root / "say_overrides.json", json.dumps(ov, indent=1, ensure_ascii=False) + "\n")
        return span.model_dump(mode="json", exclude_none=True)

    def reassign_speaker(self, body: dict) -> dict:
        source, target = body["source"], body["target"]
        chapter_id = body.get("chapter_id")
        chars = self.wd.read_characters()
        characters = {c.id: c for c in chars.characters} if chars else {}
        if source not in characters or target not in {*characters, NARRATOR_ID} or source == target:
            raise ValueError("Choose two different speakers")
        if chapter_id is not None:
            self._chapter(chapter_id)
        updates = []
        counts: Counter[str] = Counter()
        moved = 0
        for chapter in self.wd.read_chapters().chapters:
            if not self.wd.spans_path(chapter.id).is_file():
                continue
            spans = self.wd.read_spans(chapter.id)
            changed = 0
            for span in spans:
                if span.speaker == source and chapter_id in (None, chapter.id):
                    span.speaker, span.locked = target, True
                    changed += 1
                if span.kind == SpanKind.dialogue and span.speaker in (source, target):
                    counts[span.speaker] += 1
            if changed:
                moved += changed
                updates.append((chapter.id, spans))
        if updates:
            for cid, spans in updates:
                self.wd.write_spans(cid, spans)
            for cid in {source, target} & characters.keys():
                character = characters[cid]
                character.line_count = counts[cid]
                character.speaks |= counts[cid] > 0
            self.wd.write_characters(chars)
        return {"moved": moved, "chapters": [cid for cid, _ in updates]}

    def save_cast(self, body: dict) -> dict:
        cast, path = self._cast()
        who, voice = body["character"], body.get("voice") or None
        if voice and voice not in self.voices.voices:
            raise ValueError(f"unknown voice {voice}")
        if voice:
            cast.roles[who] = voice
        else:
            cast.roles.pop(who, None)  # back to automatic casting on the next run
        cast.auto.pop(who, None)
        self.wd.write_cast(cast, path)
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

    def render_span(self, span_id: str) -> dict:
        cid = span_id.split("-")[0]
        ch = self._chapter(cid)
        speech = self._speech(ch)
        renderer = self.renderer(prepare=True)
        segs = renderer.plan(ch, speech=speech)
        seg = next((s for s in segs if span_id in s.span_ids), None)
        if seg is None:
            raise KeyError(f"no segment found for span {span_id}")
        renderer.synthesize([seg])
        if self.cfg.audio.level_match:
            renderer.measure([seg])
        return {
            "ok": True,
            "span_id": span_id,
            "key": seg.cache_key,
            "voice": seg.voice,
            "text": seg.text,
            "audio_url": f"/audio/segment/{seg.cache_key}.wav",
        }

    def render_chapter(self, chapter_id: str) -> dict:
        ch = self._chapter(chapter_id)
        speech = self._speech(ch)
        renderer = self.renderer(prepare=True)
        segs = renderer.plan(ch, speech=speech)
        renderer.synthesize(segs)
        gains = {}
        if self.cfg.audio.level_match:
            renderer.measure(segs)
            gains = renderer.voice_gains(segs, partial=True)
        ca = renderer.assemble(ch, segs, gains)
        prior = [c for c in self.wd.read_manifest().chapters if c.chapter_id != chapter_id] if self.wd.manifest_path.exists() else []
        manifest_chapters = sorted([*prior, ca], key=lambda c: c.chapter_id)
        self.wd.write_manifest(AudioManifest(sample_rate=self.cfg.audio.sample_rate, chapters=manifest_chapters))
        return {
            "ok": True,
            "chapter_id": chapter_id,
            "duration_ms": ca.duration_ms,
            "segments": ca.segments,
            "audio_url": f"/audio/chapter/{chapter_id}.wav",
        }

    def render(self, body: dict) -> dict:
        if "span_id" in body and body["span_id"]:
            return self.render_span(body["span_id"])
        if "chapter_id" in body and body["chapter_id"]:
            return self.render_chapter(body["chapter_id"])
        raise ValueError("expected span_id or chapter_id in request body")

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
        return read_say_overrides(self.wd)

    def _character_ids(self) -> set[str]:
        chars = self.wd.read_characters()
        return {c.id for c in chars.characters} if chars else set()

    def _chapter(self, cid: str):
        for c in self.wd.read_chapters().chapters:
            if c.id == cid:
                return c
        raise KeyError(f"no chapter {cid}")


_LOCAL = {"localhost", "127.0.0.1", "::1"}


def proxy_ip(value: str) -> str:
    address = ip_address(value)
    return str(getattr(address, "ipv4_mapped", None) or address)


def request_allowed(review, peer: str, host: str, origin: str | None, write: bool) -> bool:
    try:
        parsed = urlsplit("//" + host)
        _ = parsed.port
    except ValueError:
        return False
    if not parsed.hostname or parsed.netloc != host or parsed.username is not None:
        return False
    if parsed.hostname in review.public_domains:
        if (host.lower() not in (parsed.hostname, f"{parsed.hostname}:443")
                or proxy_ip(peer) not in {proxy_ip(ip) for ip in review.trusted_proxy_ips}):
            return False
        return (origin is None and not write) or origin == f"https://{parsed.hostname}"
    if parsed.hostname not in _LOCAL:
        return False
    if review.public_domains and not ip_address(proxy_ip(peer)).is_loopback:
        return False
    return origin is None or origin == "http://" + host


def make_handler(book: Book | None = None):
    page = resources.files(__package__).joinpath("index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "incant-review"
        timeout = 30  # seconds per socket read or write, from the first byte: slow or idle clients are dropped

        def log_message(self, fmt, *args):  # quiet: only errors
            if len(args) > 1 and str(args[1])[:1] in "45":
                super().log_message(fmt, *args)

        @contextmanager
        def response(self):
            try:
                yield
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                self.close_connection = True

        def _security_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")  # no framing by other sites (clickjacking)
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")

        def _send(self, status: int, body: bytes, ctype: str):
            with self.response():
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _file(self, path: Path | None, ctype: str = "audio/wav", *, disposition: str | None = None):
            if path is None:
                return self._json({"error": "not found"}, 404)
            with path.open("rb") as source, self.response():
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(os.fstat(source.fileno()).st_size))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                if disposition:
                    self.send_header("Content-Disposition", disposition)
                self.end_headers()
                while chunk := source.read(65536):
                    self.wfile.write(chunk)

        def _body(self) -> dict:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json")
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                raise ValueError("Request body is empty or too large")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            return body

        def _allowed(self, write: bool) -> bool:
            """The dashboard's rule (request_allowed): a web page the user visits, or a rebound DNS name, can't
            drive the page or spend the operator's TTS and LLM keys."""
            if request_allowed(book.cfg.review, self.client_address[0], self.headers.get("Host", ""),
                               self.headers.get("Origin"), write):
                return True
            self._json({"error": "Host, origin, or proxy address is not allowed"}, 403)
            return False

        def do_GET(self):
            if self._allowed(False):
                self.review_get(unquote(urlparse(self.path).path), book)

        def review_get(self, path, selected):
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/api/book":
                    return self._json(selected.book())
                if m := re.fullmatch(r"/api/chapter/(\d{4})", path):
                    return self._json(selected.chapter(m.group(1)))
                if path == "/api/lexicon":
                    return self._json(selected.lexicon())
                if m := re.fullmatch(r"/audio/segment/([0-9a-f]{32})\.wav", path):
                    return self._file(selected.segment_audio(m.group(1)))
                if m := re.fullmatch(r"/audio/chapter/(\d{4})\.wav", path):
                    return self._file(selected.chapter_audio(m.group(1)))
                if m := re.fullmatch(r"/audio/voice/([\w.-]+)\.wav", path):
                    return self._file(selected.voice_sample(m.group(1)))
                self._json({"error": "not found"}, 404)
            except KeyError as e:
                self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self._allowed(True):
                return
            path = urlparse(self.path).path
            try:
                self.review_post(path, book, self._body())
            except (KeyError, ValueError, TypeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def review_post(self, path, selected, body):
            handlers = {"/api/span": selected.save_span, "/api/cast": selected.save_cast,
                        "/api/character": selected.save_character, "/api/lexicon": selected.save_lexicon,
                        "/api/render": selected.render, "/api/reassign": selected.reassign_speaker}
            if path not in handlers:
                return self._json({"error": "not found"}, 404)
            with selected.write_lock:
                result = handlers[path](body)
            self._json(result)

    return Handler


def serve(cfg: Config, wd: WorkDir, host: str, port: int, published: bool = False) -> None:
    """published: running in a container whose port compose publishes on the host (compose.desktop.yaml), so
    the page is opened at the host's localhost, and who can reach it is decided by the port mapping."""
    if bool(cfg.review.public_domains) != bool(cfg.review.trusted_proxy_ips):
        raise ValueError("Configure both review.public_domains and review.trusted_proxy_ips for Caddy access")
    if host not in _LOCAL and not published and not cfg.review.public_domains:
        raise ValueError("LAN binding requires review.public_domains and review.trusted_proxy_ips "
                         "(an authenticating reverse proxy); from another computer, use ssh -L 8765:127.0.0.1:8765")
    httpd = ThreadingHTTPServer((host, port), make_handler(Book(cfg, wd)))
    shown = "localhost" if published or host in ("127.0.0.1", "localhost") else host
    print(f"review page for '{wd.read_meta().title}': http://{shown}:{port}/   (Ctrl-C to stop)", flush=True)
    if published:
        print("  (from the container; open it on this computer — needs `docker compose run --service-ports`)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
