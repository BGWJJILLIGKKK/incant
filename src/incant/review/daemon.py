"""Local pipeline dashboard and persistent job queue."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from queue import Queue
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..config import Config, config_paths, load_config
from ..pipeline import PUBLISHING_MODES, discover, instantiate, stages_for, validate_stage_range
from ..ebook import draft_path, ensure_draft, output_record, require_conversion, source_format
from ..workdir import WorkDir, atomic_write, refuse_protected
from .ebook import ebook_data, save_ebook
from .server import _LOCAL, Book, make_handler, proxy_ip, request_allowed  # noqa: F401 (re-exported)

_JOB_ID = re.compile(r"[a-f0-9]{32}")
_ACTIVE = {"queued", "running"}
_LOG_BYTES = 64 * 1024
_UPLOAD_BYTES = 256 * 1024 * 1024


class BusyError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _under(root: Path, value: str) -> Path:
    root = root.resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path must be within {root}")
    return path


class Dashboard:
    def __init__(self, cfg: Config, config: Path | None = None, profile: str | None = None,
                 *, allow_paths: tuple[Path, ...] = ()):
        self.cfg = cfg
        self.config = config.resolve() if config else None
        self.profile = profile
        package = Path(__file__).resolve().parents[1]
        checkout = package.parent.parent
        default_code = checkout if (checkout / "pyproject.toml").is_file() else package
        self.code_dir = Path(os.environ.get("INCANT_HOME") or default_code).resolve()
        env_roots = (Path(value) for value in os.environ.get("INCANT_PATH_ROOTS", "").split(os.pathsep) if value.strip())
        extra = tuple(path.expanduser().resolve() for path in (*env_roots, *allow_paths))
        if any(path.exists() and not path.is_dir() for path in extra):
            raise ValueError("Each allowed path root must be a directory")
        self.work_root = cfg.paths.work_dir.resolve()
        self.output_root = cfg.paths.output_dir.resolve()
        self.path_roots = tuple(dict.fromkeys((cfg.paths.data_dir.resolve(), self.code_dir, self.work_root, *extra)))
        self.stages = discover(None)
        self.root = cfg.paths.data_dir / ".incant" / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lockfile = (self.root / ".daemon.lock").open("a")
        if os.name == "posix":
            import fcntl

            try:
                fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self.lockfile.close()
                raise RuntimeError("An Incant dashboard already owns this job directory") from error
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.queue: Queue[str | None] = Queue()
        self.jobs: dict[str, dict] = {}
        self.books: dict[str, tuple[int, Book]] = {}
        self.process: subprocess.Popen | None = None
        self.stopping = False
        self.running: str | None = None
        for path in sorted(self.root.glob("*.json")):
            if not _JOB_ID.fullmatch(path.stem):
                continue
            try:
                job = json.loads(path.read_text())
                if not isinstance(job, dict) or job.get("id") != path.stem:
                    continue
            except (ValueError, OSError):
                continue
            if job["state"] == "running":
                job.update(state="interrupted", finished=_now())
                self._save(job)
            self.jobs[job["id"]] = job
        self.jobs = dict(sorted(self.jobs.items(), key=lambda item: item[1]["created"]))
        for job in self.jobs.values():
            if job["state"] == "queued":
                self.queue.put(job["id"])
        self.worker = threading.Thread(target=self._work, name="incant-pipeline", daemon=True)
        self.worker.start()

    def _save(self, job: dict) -> None:
        atomic_write(self.root / f'{job["id"]}.json', json.dumps(job, ensure_ascii=False) + "\n")

    @contextmanager
    def exclusive(self):
        if not self.operation.acquire(blocking=False):
            raise BusyError("A pipeline or review render is running. Try again when it finishes.")
        try:
            yield
        finally:
            self.operation.release()

    def _approved(self, path: Path, *, also: tuple[Path, ...] = ()) -> Path:
        resolved = path.expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in (*self.path_roots, *also)):
            raise ValueError(f"Path is outside the daemon's allowed roots: {resolved}")
        return resolved

    def _check_config_paths(self, cfg: Config) -> None:
        self._approved(cfg.paths.data_dir)
        self._approved(cfg.paths.work_dir)
        self._approved(cfg.paths.output_dir, also=(self.output_root,))

    def _output_path(self, path: Path) -> Path:
        refuse_protected(path, "An output file")
        resolved = self._approved(self.cfg.paths.output_dir / path.expanduser(), also=(self.output_root,))
        refuse_protected(resolved, "An output file")  # also a symlink that points at one
        return resolved

    def _source(self, source: str) -> str:
        if source.startswith(("https://", "http://")):
            url = urlsplit(source)
            if not url.hostname or url.username is not None:
                raise ValueError("Enter an HTTP(S) URL without embedded credentials")
            return source
        refuse_protected(source, "A book source")
        if Path(source).name == source and not source.startswith("."):
            if (self.cfg.paths.work_dir / source / "meta.json").is_file():
                return str(_under(self.cfg.paths.work_dir, source))
        source_path = Path(source).expanduser()
        if source_path.is_absolute():
            candidates = [self._approved(source_path)]
        else:
            candidates = [root / source_path for root in dict.fromkeys((self.cfg.paths.data_dir, *self.path_roots))]
        for candidate in candidates:
            if candidate.exists():
                resolved = self._approved(candidate)
                refuse_protected(resolved, "A book source")  # also a symlink that points at one
                return str(resolved)
        raise ValueError("Source must be a URL, an existing book, or an existing path in an allowed directory")

    def arguments(self, body: dict) -> list[str]:
        from ..cli import _parse_chapters

        source = body.get("source")
        if not isinstance(source, str):
            raise ValueError("Choose an input")
        source = source.strip()
        if not source:
            raise ValueError("Choose an input")
        source = self._source(source)
        mode = body.get("mode", "run")
        if mode not in ("run", "plan", "check"):
            raise ValueError("Unknown job mode")
        publishing = body.get("publishing", "audiobook")
        if publishing not in PUBLISHING_MODES:
            raise ValueError("Unknown publishing mode")
        stages = stages_for(self.stages, publishing)
        args = ["--mode", "ebook"] if publishing == "ebook" else []
        if self.config is not None:
            args += ["--config", str(self.config)]
        if mode != "run":
            args.append("--dry-run" if mode == "plan" else "--check")
        if publishing == "ebook" and (body.get("chapters") or body.get("llm")):
            raise ValueError("Chapter selection and LLM profiles are audiobook options")
        profile = (body.get("llm") or self.profile) if publishing == "audiobook" else None
        if profile:
            if profile not in self.cfg.llm_profiles:
                raise ValueError(f"Unknown LLM profile {profile}")
            args += ["--llm", profile]
        names = [stage.name for stage in stages if not getattr(stage.module, "PRE", False)]
        start, end = body.get("start") or None, body.get("stop") or None
        validate_stage_range(names, start, end)
        for flag, value in (("--from", start), ("--until", end)):
            if value:
                args += [flag, value]
        chapters = body.get("chapters")
        if chapters:
            _parse_chapters(chapters)
            args += ["--chapters", chapters]
        force = body.get("force")
        if force:
            if set(part.strip() for part in force.split(",")) - {*names, "all"}:
                raise ValueError("Force must name pipeline stages or all")
            args += ["--force", force]
        slug = body.get("slug")
        if slug:
            if not re.fullmatch(r"[\w][\w.-]*", slug) or slug in {".", ".."}:
                raise ValueError("Book slug must be a single directory name")
            _under(self.cfg.paths.work_dir, slug)
            args += ["--slug", slug]
        if output := body.get("output"):
            target = self._output_path(Path(output))
            if publishing == "ebook" and (target.is_dir() or not target.suffix):
                self._output_path(target / "ebooks")
            args += ["--out", str(target)]
        elif publishing == "ebook":
            self._output_path(self.cfg.paths.output_dir / "ebooks")
        allowed = {stage.name: stage.methods for stage in stages}
        methods = []
        for stage, method in body.get("methods", {}).items():
            if method:
                if method not in allowed.get(stage, []):
                    raise ValueError(f"Unknown method {stage}={method}")
                methods.append(f"{stage}={method}")
        return [*args, "--", source, *methods]

    @staticmethod
    def _public(job: dict) -> dict:
        return {k: v for k, v in job.items() if k != "args"}

    def submit(self, body: dict) -> dict:
        with self.lock:
            args = self.arguments(body)
            job = {"id": uuid.uuid4().hex, "source": body["source"], "mode": body.get("mode", "run"),
                   "publishing": body.get("publishing", "audiobook"),
                   "created": _now(), "state": "queued", "args": args}
            self._save(job)
            self.jobs[job["id"]] = job
            self.queue.put(job["id"])
            return self._public(job)

    def jobs_list(self) -> list[dict]:
        with self.lock:
            return [self._public(job) for job in reversed(list(self.jobs.values()))]

    def clear_finished(self) -> int:
        with self.lock:
            finished = [key for key, job in self.jobs.items()
                        if job["state"] not in _ACTIVE and key != self.running]
            for key in finished:
                (self.root / f"{key}.log").unlink(missing_ok=True)
                (self.root / f"{key}.json").unlink(missing_ok=True)
                del self.jobs[key]
            return len(finished)

    def job(self, job_id: str) -> dict:
        with self.lock:
            job = self._public(self.jobs[job_id])
            try:
                with (self.root / f"{job_id}.log").open("rb") as log:
                    log.seek(max(0, os.fstat(log.fileno()).st_size - _LOG_BYTES))
                    job["log"] = log.read(_LOG_BYTES).decode(errors="replace")
            except FileNotFoundError:
                pass
        return job

    def _signal(self, sig: int) -> None:
        if self.process and self.process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(self.process.pid, sig)
                elif sig == signal.SIGTERM:
                    self.process.terminate()
                else:
                    self.process.kill()
            except ProcessLookupError:
                pass

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] in _ACTIVE:
                job.update(state="cancelled", finished=_now())
                self._save(job)
                if self.running == job_id:
                    self._signal(signal.SIGTERM)
                    timer = threading.Timer(3, self._kill_cancelled, args=(job_id,))
                    timer.daemon = True
                    timer.start()
            return self._public(job)

    def _kill_cancelled(self, job_id: str) -> None:
        with self.lock:
            if self.running == job_id:
                self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))

    def _work(self) -> None:
        while (job_id := self.queue.get()) is not None:
            with self.operation:
                with self.lock:
                    if self.stopping:
                        return
                    job = self.jobs.get(job_id)
                    if job is None or job["state"] != "queued":
                        continue
                    job.update(state="running", started=_now())
                    self._save(job)
                    self.running = job_id
                code = None
                with (self.root / f"{job_id}.log").open("ab", buffering=0) as log:
                    try:
                        with self.lock:
                            if job["state"] == "running" and not self.stopping:
                                args = job["args"]
                                split = args.index("--")
                                self._source(args[split + 1])
                                options = args[:split]
                                if "--out" in options:
                                    self._output_path(Path(options[options.index("--out") + 1]))
                                self.process = subprocess.Popen(
                                    [sys.executable, "-u", "-m", "incant", *job["args"]],
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
                                )
                        if self.process:
                            code = self.process.wait()
                    except (OSError, ValueError) as error:
                        log.write(f"Could not launch Incant: {error}\n".encode())
                with self.lock:
                    if job["state"] == "running":
                        state = "interrupted" if self.stopping else "succeeded" if code == 0 else "failed"
                        job.update(state=state, finished=_now(), exit_code=code)
                        self._save(job)
                    self.process = self.running = None
                    self.books.clear()

    def close(self) -> None:
        with self.lock:
            self.stopping = True
            self._signal(signal.SIGTERM)
            self.queue.put(None)
        self.worker.join(timeout=3)
        if self.worker.is_alive():
            with self.lock:
                self._signal(getattr(signal, "SIGKILL", signal.SIGTERM))
            self.worker.join(timeout=2)
        self.lockfile.close()

    def _config_targets(self) -> dict[str, tuple[Path, Path]]:
        _, data, files = config_paths(self.config)
        base = files[0]
        target = base
        if self.config is None and data is not None:
            replacement = data / "incant.toml"
            if replacement.is_file() or not os.access(base.parent, os.W_OK):
                target = replacement
        result = {"base": (target, base), "local": (files[-1], files[-1])}
        if len(files) > 2 and files[1].exists():
            result["installation-local"] = (files[1], files[1])
        return result

    def config_files(self) -> list[dict]:
        with self.lock:
            files = []
            for key, (target, fallback) in self._config_targets().items():
                read = target if target.exists() else fallback
                files.append({"id": key, "path": str(target), "text": read.read_text() if read.is_file() else "",
                              "writable": os.access(target.parent, os.W_OK)})
            return files

    def save_config(self, body: dict) -> dict:
        with self.exclusive(), self.lock:
            target, _ = self._config_targets()[body["id"]]
            text = body["text"]
            if not isinstance(text, str):
                raise ValueError("Configuration must be TOML text")
            candidate = load_config(self.config, current_llm=self.profile, edits={target: text}, publish_secrets=False)
            self._check_config_paths(candidate)
            if candidate.review != self.cfg.review:
                raise ValueError("Change review listener and proxy settings in the TOML file on disk and restart Incant")
            if candidate.paths.voices_dir != self.cfg.paths.voices_dir:
                self._approved(candidate.paths.voices_dir)
            allowed = {stage.name: stage for stage in self.stages}
            for stage, name in candidate.pipeline.items():
                if not (stage == "ingest" and name == "auto"):
                    if stage not in allowed or name not in allowed[stage].methods:
                        raise ValueError(f"Unknown default method {stage}={name}")
            for stage in self.stages:
                for name in stage.methods:
                    if candidate.method_settings(stage.name, name) or candidate.pipeline.get(stage.name) == name:
                        instantiate(candidate, stage, name)
            atomic_write(target, text)
            self.cfg = load_config(self.config, current_llm=self.profile)
            self.books.clear()
            return {"path": str(target)}

    def settings(self) -> dict:
        with self.lock:
            return {"data_dir": str(self.cfg.paths.data_dir), "output_dir": str(self.cfg.paths.output_dir),
                    "path_roots": [str(path) for path in self.path_roots],
                    "pipeline": self.cfg.pipeline, "profiles": sorted(self.cfg.llm_profiles),
                    "stages": {s.name: s.methods for s in self.stages},
                    "publishing_stages": {mode: [s.name for s in stages_for(self.stages, mode)]
                                          for mode in PUBLISHING_MODES}}

    def library(self) -> list[dict]:
        books = []
        for meta in sorted(self.cfg.paths.work_dir.glob("*/meta.json")):
            slug = meta.parent.name
            try:
                wd = WorkDir(_under(self.cfg.paths.work_dir, slug))
                info = wd.read_meta()
                manifest = wd.read_manifest().chapters if wd.manifest_path.is_file() else []
                books.append({"slug": slug, "title": info.title, "author": info.author,
                              "chapters": len(wd.read_chapters().chapters) if wd.chapters_path.is_file() else 0,
                              "rendered": len(manifest),
                              "ebook_allowed": source_format(info) != "epub",
                              "ebook_draft": draft_path(wd).is_file(),
                              "ebook_exports": [{"name": path.name, "url": f"/ebook-exports/{quote(slug, safe='')}/{i}"}
                                                for i, path in enumerate(self.exports(slug, ebook=True))],
                              "exports": [{"name": path.name, "url": f"/exports/{quote(slug, safe='')}/{i}"}
                                          for i, path in enumerate(self.exports(slug))]})
            except (ValueError, OSError):
                continue
        return books

    def exports(self, slug: str, *, ebook: bool = False) -> list[Path]:
        wd = WorkDir(_under(self.cfg.paths.work_dir, slug))
        path = output_record(wd) if ebook else wd.root / "output.json"
        if not path.is_file():
            return []
        files = json.loads(path.read_text()).get("files", [])
        return [p for name in files if (p := Path(name).resolve()).is_file() and
                any(p.is_relative_to(root) for root in (*self.path_roots, self.output_root))]

    def ebook_workdir(self, slug: str) -> WorkDir:
        wd = WorkDir(_under(self.cfg.paths.work_dir, slug))
        if not wd.meta_path.is_file():
            raise KeyError("Book is not in the library")
        require_conversion(wd.read_meta())
        return wd

    def book(self, slug: str) -> Book:
        with self.lock:
            wd = WorkDir(_under(self.cfg.paths.work_dir, slug))
            if not wd.meta_path.is_file() or not wd.chapters_path.is_file():
                raise KeyError("Book is not ready for review")
            path = wd.root / "pipeline.json"
            stamp = path.stat().st_mtime_ns if path.is_file() else 0
            existing = self.books.get(slug)
            if existing is None or existing[0] != stamp:
                existing = self.books[slug] = stamp, Book(self.cfg, wd)
            return existing[1]

    def upload(self, name: str, size: int, stream) -> dict:
        if not name or name.startswith(".") or Path(name).name != name or "\\" in name or "\x00" in name:
            raise ValueError("Invalid upload filename")
        if not 0 < size <= _UPLOAD_BYTES:
            raise ValueError("Uploads must be between 1 byte and 256 MiB")
        path = _under(self.cfg.paths.data_dir, f"uploads/{uuid.uuid4().hex}/{name}")
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            with path.open("xb") as out:
                remaining = size
                while remaining:
                    chunk = stream.read(min(remaining, 65536))
                    if not chunk:
                        raise ValueError("Upload ended early")
                    out.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            path.parent.rmdir()
            raise
        return {"source": str(path.relative_to(self.cfg.paths.data_dir))}




def make_dashboard_handler(dashboard: Dashboard):
    review = dashboard.cfg.review.model_copy(deep=True)
    page = resources.files(__package__).joinpath("dashboard.html").read_bytes()
    script = resources.files(__package__).joinpath("dashboard.js").read_bytes()
    ebook_page = resources.files(__package__).joinpath("ebook.html").read_bytes()
    ebook_script = resources.files(__package__).joinpath("ebook.js").read_bytes()
    ebook_route = re.compile(r"/ebook/([^/]+)(/.*)?")
    review_route = re.compile(r"/review/([^/]+)(/.*)?")
    export_route = re.compile(r"/(exports|ebook-exports)/([^/]+)/(\d+)")

    class Handler(make_handler()):
        server_version = "incant"

        def _dispatch(self, write: bool):
            self.connection.settimeout(30)
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if not request_allowed(review, self.client_address[0], host, origin, write):
                return self._json({"error": "Host, origin, or proxy address is not allowed"}, 403)
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if match := ebook_route.fullmatch(path):
                slug, subpath = match.groups()
                if not write and subpath in (None, "/"):
                    return self._send(200, ebook_page, "text/html; charset=utf-8")
                if subpath == "/api/draft":
                    wd = dashboard.ebook_workdir(slug)
                    if write:
                        with dashboard.exclusive():
                            wd = dashboard.ebook_workdir(slug)
                            return self._json(save_ebook(dashboard, wd, self._body()))
                    if draft_path(wd).is_file():
                        return self._json(ebook_data(wd))
                    with dashboard.exclusive():
                        wd = dashboard.ebook_workdir(slug)
                        ensure_draft(wd)
                        return self._json(ebook_data(wd))
            if match := review_route.fullmatch(path):
                slug, subpath = match.groups()
                if not write:
                    if not subpath:
                        with self.response():
                            self.send_response(302)
                            self.send_header("Location", parsed.path + "/")
                            self.end_headers()
                        return
                    return self.review_get(subpath, None if subpath == "/" else dashboard.book(slug))
                with dashboard.exclusive():
                    return self.review_post(subpath, dashboard.book(slug), self._body())
            if write:
                if path == "/api/upload":
                    name = parse_qs(parsed.query).get("name", [""])[0]
                    return self._json(dashboard.upload(name, int(self.headers.get("Content-Length", "0")), self.rfile), 201)
                if path == "/api/jobs":
                    return self._json(dashboard.submit(self._body()), 202)
                if path == "/api/jobs/clear-finished":
                    self._body()
                    return self._json({"removed": dashboard.clear_finished()})
                if path == "/api/config":
                    return self._json(dashboard.save_config(self._body()))
                if match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", path):
                    self._body()
                    return self._json(dashboard.cancel(match[1]))
            else:
                if path == "/":
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/ebook.js":
                    return self._send(200, ebook_script, "text/javascript; charset=utf-8")
                if path == "/dashboard.js":
                    return self._send(200, script, "text/javascript; charset=utf-8")
                if path == "/api/settings":
                    return self._json(dashboard.settings())
                if path == "/api/config":
                    return self._json(dashboard.config_files())
                if path == "/api/books":
                    return self._json(dashboard.library())
                if path == "/api/jobs":
                    return self._json(dashboard.jobs_list())
                if match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})", path):
                    return self._json(dashboard.job(match[1]))
                if match := export_route.fullmatch(path):
                    ebook = match[1] == "ebook-exports"
                    files = dashboard.exports(match[2], ebook=ebook)
                    index = int(match[3])
                    if index >= len(files):
                        raise KeyError("Export not found")
                    artifact = files[index]
                    return self._file(artifact, "application/epub+zip" if ebook else "application/octet-stream",
                                      disposition="attachment; filename*=UTF-8''" + quote(artifact.name))
            self._json({"error": "Not found"}, 404)

        def _handle(self, write: bool):
            try:
                self._dispatch(write)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True
            except KeyError as error:
                self._json({"error": str(error)}, 404)
            except BusyError as error:
                self._json({"error": str(error)}, 409)
            except (ValueError, TypeError, AttributeError) as error:
                self._json({"error": str(error)}, 400)
            except (RuntimeError, OSError) as error:
                self._json({"error": str(error)}, 500)

        def do_GET(self):
            self._handle(False)

        def do_POST(self):
            self._handle(True)

    return Handler


def serve_dashboard(cfg: Config, config: Path | None, profile: str | None, host: str, port: int,
                    *, published: bool = False, allow_paths: tuple[Path, ...] = ()) -> None:
    if bool(cfg.review.public_domains) != bool(cfg.review.trusted_proxy_ips):
        raise ValueError("Configure both review.public_domains and review.trusted_proxy_ips for Caddy access")
    if host not in _LOCAL and not published and not cfg.review.public_domains:
        raise ValueError("LAN binding requires review.public_domains and review.trusted_proxy_ips")
    if ":" in host:
        import socket

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
    else:
        Server = ThreadingHTTPServer
    dashboard = Dashboard(cfg, config, profile, allow_paths=allow_paths)
    previous = None
    try:
        with Server((host, port), make_dashboard_handler(dashboard)) as server:
            def stop(_signum, _frame):
                raise KeyboardInterrupt

            if threading.current_thread() is threading.main_thread():
                previous = signal.signal(signal.SIGTERM, stop)
            bind = "localhost" if host in _LOCAL else f"[{host}]" if ":" in host else host
            print(f"Incant dashboard at http://{bind}:{server.server_port}/", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        dashboard.close()
