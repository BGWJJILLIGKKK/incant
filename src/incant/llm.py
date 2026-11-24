"""OpenAI-compatible chat client with schema-constrained JSON output and a response cache.

Stages 2b/3b build on `chat_json`. Responses are cached on disk by a hash of the
full request, so re-runs are free and deterministic (temperature defaults to 0).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

try:
    import fcntl
except ImportError:
    fcntl = None

import httpx
from pydantic import BaseModel

from .config import LLMConfig, secret
from .lanes import llm_endpoint
from .workdir import atomic_write

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class TokenBudget:
    """Admission control for the server's shared KV cache: a request reserves its worst case
    (estimated prompt + max_tokens) before it is sent and releases it when done."""

    def __init__(self, total: int, lock_path: Path | None = None):
        self.total = total
        self.free = total
        self.cond = threading.Condition()
        self.lock_path = None
        if lock_path and fcntl is not None:
            try:
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.touch(exist_ok=True)
                self.lock_path = lock_path
            except OSError:
                pass

    def acquire(self, n: int) -> int:
        n = min(n, self.total)  # an oversized request runs alone rather than never
        with self.cond:
            self.cond.wait_for(lambda: self.free >= n)
            self.free -= n
        try:
            if self.lock_path is not None:
                self._acquire_cross_process(n)
        except BaseException:
            self._release_local(n)
            raise
        return n

    def release(self, n: int) -> None:
        try:
            if self.lock_path is not None:
                self._release_cross_process(n)
        finally:
            self._release_local(n)

    def _release_local(self, n: int) -> None:
        with self.cond:
            self.free += n
            self.cond.notify_all()

    @contextmanager
    def _reservation_file(self):
        # flock is attached to the open file description. Each transaction needs its own
        # descriptor so threads in one process contend for the lock just like separate processes.
        with self.lock_path.open("a+b") as file:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            yield file

    @staticmethod
    def _read_reservations(file) -> list[dict]:
        file.seek(0)
        raw = file.read().decode("utf-8", errors="replace").lstrip()
        if not raw:
            return []
        try:
            # Older Incant builds could leave extra JSON after a complete reservation list.
            entries, _ = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            return []
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict)
                and type(entry.get("pid")) is int and entry["pid"] > 0
                and type(entry.get("tokens")) is int and entry["tokens"] >= 0
                and type(entry.get("time")) in (int, float)]

    @staticmethod
    def _write_reservations(file, entries: list[dict]) -> None:
        file.seek(0)
        file.truncate()
        file.write(json.dumps(entries).encode())
        file.flush()

    def _acquire_cross_process(self, n: int) -> None:
        while True:
            with self._reservation_file() as file:
                reservations = self._read_reservations(file)
                now = time.time()
                active = []
                for r in reservations:
                    if now - r["time"] >= 600:
                        continue
                    try:
                        os.kill(r["pid"], 0)
                    except ProcessLookupError:
                        continue
                    except PermissionError:
                        pass  # an existing process owned by another user
                    active.append(r)
                used = sum(r["tokens"] for r in active)
                if used + n <= self.total or not active:
                    active.append({"pid": os.getpid(), "tokens": n, "time": now})
                    self._write_reservations(file, active)
                    return
            time.sleep(0.05)

    def _release_cross_process(self, n: int) -> None:
        with self._reservation_file() as file:
            reservations = self._read_reservations(file)
            pid = os.getpid()
            for i, r in enumerate(reservations):
                if r["pid"] == pid and r["tokens"] == n:
                    reservations.pop(i)
                    break
            self._write_reservations(file, reservations)


_budgets: dict[object, TokenBudget] = {}
_budgets_lock = threading.Lock()


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 64  # conservative for English (~4 chars/token) plus chat-template overhead


def prompt_char_budget(cfg: LLMConfig, system: str, schema: type[BaseModel], max_tokens: int,
                       ceiling: int | None = None) -> int:
    """Estimated room for user text, after allowing for instructions, schema and completion."""
    limit = min(cfg.context_tokens, ceiling) if ceiling is not None else cfg.context_tokens
    reserve = max(max_tokens, cfg.min_max_tokens)
    if cfg.dialect == "anthropic":
        from .llm_anthropic import MIN_MAX_TOKENS

        reserve = max(reserve, MIN_MAX_TOKENS)
    return max(0, (limit - reserve - estimate_tokens(system + json.dumps(schema.model_json_schema())) - 128) * 3)


class LLM:
    def __init__(self, cfg: LLMConfig, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = cache_dir
        with _budgets_lock:  # one budget per server/model/context, shared by clients
            ep = llm_endpoint(cfg)
            key = (ep, cfg.model, cfg.context_tokens)
            lock_dir = Path(os.environ.get("INCANT_LOCK_DIR", Path("/tmp/incant_locks")))
            lock_name = hashlib.sha256(f"{ep}:{cfg.model}:{cfg.context_tokens}".encode()).hexdigest()[:16] + ".lock"
            self.budget = _budgets.setdefault(key, TokenBudget(cfg.context_tokens, lock_path=lock_dir / lock_name))
        key = (secret(cfg.api_key_env) or "") if cfg.api_key_env else "none"  # local servers ignore it
        self._anthropic = None
        self.http = httpx.Client(
            base_url=cfg.base_url.rstrip("/"), timeout=cfg.timeout_s, headers={"Authorization": f"Bearer {key}"}
        )

    def body(self, system: str, user: str, schema: type[BaseModel], think: bool, max_tokens: int) -> dict:
        """The request. For the llama.cpp dialect it is exactly what earlier versions sent, so cached answers
        (keyed by the request) stay valid."""
        c = self.cfg
        js = schema.model_json_schema() if c.schema_style == "full" else simple_schema(schema.model_json_schema())
        if c.structured == "json_object":
            system += ("\n\nAnswer with one JSON object matching this JSON schema, and nothing else:\n" + json.dumps(js))
            fmt: dict = {"type": "json_object"}
        else:
            fmt = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": js, "strict": True}}
        body = {
            "model": c.model,
            "temperature": c.temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": fmt,
        }
        if c.dialect == "llama.cpp":
            body["chat_template_kwargs"] = {"enable_thinking": think}
            if think:
                body |= c.thinking_sampling
        elif think:  # standard sampling fields only
            body |= {k: v for k, v in c.thinking_sampling.items() if k in ("temperature", "top_p", "seed")}
        if c.dialect == "openai" and c.reasoning_effort:
            body["reasoning_effort"] = c.reasoning_effort
        if c.min_max_tokens > body["max_tokens"]:
            body["max_tokens"] = c.min_max_tokens
        return body

    def models(self) -> list[str]:
        if self.cfg.dialect == "anthropic":
            from . import llm_anthropic

            return [m.id for m in llm_anthropic.client(self.cfg).models.list()]
        r = self.http.get("/models")
        r.raise_for_status()
        # Gemini's OpenAI-style endpoint lists "models/<name>" but takes the bare name in requests.
        return [m["id"].removeprefix("models/") for m in r.json().get("data", [])]

    def chat_json(self, system: str, user: str, schema: type[M], *, think: bool = False, max_tokens: int = 4096) -> M:
        anthropic_api = self.cfg.dialect == "anthropic"
        if anthropic_api:
            from . import llm_anthropic

            body = llm_anthropic.request(self.cfg, system, user, schema, think, max_tokens)
        else:
            body = self.body(system, user, schema, think, max_tokens)
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        cached = self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            return schema.model_validate_json(cached.read_text())
        data = None
        sent_system = body["system"] if anthropic_api else body["messages"][0]["content"]
        need = self.budget.acquire(estimate_tokens(sent_system + user) + body["max_tokens"])
        if anthropic_api:
            try:
                if self._anthropic is None:
                    self._anthropic = llm_anthropic.client(self.cfg)
                result, used = llm_anthropic.call(self._anthropic, body, schema)
            finally:
                self.budget.release(need)
            _count(used["input"], used["output"])
            if cached:
                atomic_write(cached, result.model_dump_json())
            return result
        try:
            for attempt in range(self.cfg.retries + 1):
                try:
                    r = self.http.post("/chat/completions", json=body)
                    r.raise_for_status()
                    data = r.json()
                    break
                except httpx.HTTPError as e:
                    status = getattr(getattr(e, "response", None), "status_code", None)
                    transient = status is None or status == 429 or status >= 500  # network, rate limit, overload
                    if attempt == self.cfg.retries or not transient:
                        detail = getattr(getattr(e, "response", None), "text", "")[:200]
                        raise LLMError(f"{type(e).__name__}: {e} {detail}".strip()) from e
                    wait = min(2 ** (attempt + 1), 32)
                    try:
                        wait = max(wait, float(e.response.headers.get("retry-after", 0)))  # type: ignore[union-attr]
                    except (AttributeError, ValueError):
                        pass
                    time.sleep(wait)
        finally:
            self.budget.release(need)
        u = data.get("usage") or {}
        _count(u.get("prompt_tokens") or 0, u.get("completion_tokens") or 0)
        choice = data["choices"][0]
        content = _strip_fences(choice["message"].get("content") or "")
        if choice.get("finish_reason") == "length":
            used = data.get("usage", {}).get("completion_tokens")
            raise LLMError(f"hit max_tokens={max_tokens} ({used} generated){' while thinking' if not content else ''}")
        try:
            result = schema.model_validate_json(content)
        except ValueError as e:
            raise LLMError(f"invalid {schema.__name__} JSON: {e}\n{content[:500]}") from e
        if cached:
            atomic_write(cached, result.model_dump_json())
        return result


_LIMITS = {"maxLength", "minLength", "maxItems", "minItems", "pattern", "format"}


def simple_schema(schema: dict) -> dict:
    """$defs inlined and length/format limits dropped (servers that support only a JSON Schema subset)."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k not in _LIMITS and k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


USAGE = {"requests": 0, "input": 0, "output": 0}  # tokens sent to / received from the LLM in this process
_usage_lock = threading.Lock()


def _count(inp: int, out: int) -> None:
    with _usage_lock:
        USAGE["requests"] += 1
        USAGE["input"] += inp
        USAGE["output"] += out


def _strip_fences(s: str) -> str:
    """Some servers wrap JSON-mode answers in ```json fences."""
    t = s.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()
