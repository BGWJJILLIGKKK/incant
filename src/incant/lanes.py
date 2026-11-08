"""Batch lanes: books move through the pipeline in parallel, one book at a time per server.

Each method names the resource it waits on (Method.resource()): "ingest" for every ingest method, an LLM or
speech server's address, "kokoro" for the in-process engine, or None for quick local work. A book's stages are
cut into phases where that resource changes; quick stages join the phase before them (or the one after, at the
start). Each resource gets a lane: one worker thread that runs one book's phase at a time, in arrival order.
So while book 1 is being synthesized, book 2 can be attributed and book 3 fetched.

Safety, whatever the providers:
- Every server still sees at most one book at a time, as in a sequential batch, so the same per-book limits
  apply: [llm] concurrency and its token budget, ElevenLabs' quota check, a site's request pacing.
- Stages that use the same server share a lane: a llama-swap on one port serving both the LLM and IndexTTS is
  never asked to swap back and forth between books.
- [run] lanes = "host" (the default) goes further: every server on one machine shares a lane, since they may
  share one GPU. "endpoint" splits lanes by port; "off" runs one book at a time.
- Each book runs in one lane at a time, and its work directory is its own; ingest is one lane, so two books
  can't claim the same slug.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def endpoint(url: str) -> str:
    """'host:port' of a server URL, with localhost spelled one way: the identity of the server."""
    try:
        u = urlsplit(url if "://" in url else f"http://{url}")
        host = (u.hostname or "").lower()
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return url
    if not host:
        return url
    return f"{'localhost' if host in LOCAL_HOSTS else host}:{port}"


def llm_endpoint(llm) -> str:
    """The server an [llm] config talks to (the Claude SDK ignores a non-Anthropic base_url)."""
    if llm.dialect == "anthropic" and "anthropic" not in llm.base_url:
        return "api.anthropic.com:443"
    return endpoint(llm.base_url)


def lane_key(resource: str | None, mode: str) -> str | None:
    if resource is None:
        return None
    if mode == "off":
        return "batch"
    if mode == "host" and ":" in resource:
        return resource.rsplit(":", 1)[0]
    return resource


@dataclass
class Phase:
    lane: str
    stages: list[str]

    @property
    def first(self) -> str:
        return self.stages[0]

    @property
    def last(self) -> str:
        return self.stages[-1]


def plan_phases(steps, resource_of: Callable[[object], str | None], mode: str,
                start: str | None = None, stop: str | None = None) -> list[Phase]:
    """Cut the steps between start and stop into phases, one per change of lane."""
    names = [s.stage.name for s in steps]
    lo = names.index(start) if start in names else 0
    hi = names.index(stop) + 1 if stop in names else len(names)
    phases: list[Phase] = []
    for step in steps[lo:hi]:
        key = None if step.method == "-" else lane_key(resource_of(step), mode)
        if phases and (key is None or key == phases[-1].lane):
            phases[-1].stages.append(step.stage.name)
        elif phases and phases[-1].lane == "local":  # quick stages so far: they join this lane
            phases[-1].lane = key
            phases[-1].stages.append(step.stage.name)
        else:
            phases.append(Phase(key or "local", [step.stage.name]))
    return phases


def describe(phases: list[Phase]) -> str:
    return " → ".join(f"{p.lane} ({', '.join(p.stages)})" for p in phases)


@dataclass
class Job:
    """One book: its phases, and run(phase) to run one of them (raises on failure)."""

    key: object
    phases: list[Phase]
    run: Callable[[Phase, int], None]
    error: BaseException | None = None
    done: bool = False
    _next: int = field(default=0, repr=False)


def run_lanes(jobs: list[Job], on_finish: Callable[[Job], None] | None = None) -> None:
    """Run every job's phases in order, each phase in its lane's worker. Jobs enter their first lane in list
    order. Returns when every job has finished or failed; Ctrl-C stops waiting (the workers are daemons)."""
    lanes: dict[str, queue.Queue] = {}
    finished: queue.Queue = queue.Queue()

    def worker(q: queue.Queue) -> None:
        while True:
            job: Job = q.get()
            phase = job.phases[job._next]
            try:
                job.run(phase, job._next)
            except BaseException as e:  # noqa: BLE001 — reported per book; the batch goes on
                job.error = e
                finished.put(job)
                continue
            job._next += 1
            if job._next < len(job.phases):
                submit(job)
            else:
                job.done = True
                finished.put(job)

    def submit(job: Job) -> None:
        lane = job.phases[job._next].lane
        with lock:
            if lane not in lanes:
                lanes[lane] = queue.Queue()
                threading.Thread(target=worker, args=(lanes[lane],), name=f"lane:{lane}", daemon=True).start()
            q = lanes[lane]
        q.put(job)

    lock = threading.Lock()
    pending = 0
    for job in jobs:
        if job.phases:
            submit(job)
            pending += 1
        else:
            job.done = True
            if on_finish:
                on_finish(job)
    while pending:
        try:
            job = finished.get(timeout=0.5)  # a timeout, so Ctrl-C reaches this thread
        except queue.Empty:
            continue
        pending -= 1
        if on_finish:
            on_finish(job)
