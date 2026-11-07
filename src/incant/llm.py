"""OpenAI-compatible chat client with schema-constrained JSON output and a response cache.

Stages 2b/3b build on `chat_json`. Responses are cached on disk by a hash of the
full request, so re-runs are free and deterministic (temperature defaults to 0).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import BaseModel

from .config import LLMConfig
from .workdir import atomic_write

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class TokenBudget:
    """Admission control for the server's shared KV cache: a request reserves its worst case
    (estimated prompt + max_tokens) before it is sent and releases it when done."""

    def __init__(self, total: int):
        self.total = total
        self.free = total
        self.cond = threading.Condition()

    def acquire(self, n: int) -> int:
        n = min(n, self.total)  # an oversized request runs alone rather than never
        with self.cond:
            self.cond.wait_for(lambda: self.free >= n)
            self.free -= n
        return n

    def release(self, n: int) -> None:
        with self.cond:
            self.free += n
            self.cond.notify_all()


_budgets: dict[str, TokenBudget] = {}
_budgets_lock = threading.Lock()


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 64  # conservative for English (~4 chars/token) plus chat-template overhead


class LLM:
    def __init__(self, cfg: LLMConfig, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = cache_dir
        with _budgets_lock:  # one budget per server, shared by every client in the process
            self.budget = _budgets.setdefault(cfg.base_url, TokenBudget(cfg.context_tokens))
        key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else cfg.api_key
        self.http = httpx.Client(
            base_url=cfg.base_url.rstrip("/"), timeout=cfg.timeout_s, headers={"Authorization": f"Bearer {key}"}
        )

    def body(self, system: str, user: str, schema: type[BaseModel], think: bool, max_tokens: int) -> dict:
        """The request. For the llama.cpp dialect it is exactly what earlier versions sent, so cached answers
        (keyed by the request) stay valid."""
        c = self.cfg
        if c.structured == "json_object":
            system += ("\n\nAnswer with one JSON object matching this JSON schema, and nothing else:\n"
                       + json.dumps(schema.model_json_schema()))
            fmt: dict = {"type": "json_object"}
        else:
            fmt = {"type": "json_schema",
                   "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}
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
        return body

    def models(self) -> list[str]:
        r = self.http.get("/models")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def chat_json(self, system: str, user: str, schema: type[M], *, think: bool = False, max_tokens: int = 4096) -> M:
        body = self.body(system, user, schema, think, max_tokens)
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        cached = self.cache_dir / key[:2] / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            return schema.model_validate_json(cached.read_text())
        data = None
        need = self.budget.acquire(estimate_tokens(system + user) + max_tokens)
        try:
            for attempt in range(2):
                try:
                    r = self.http.post("/chat/completions", json=body)
                    r.raise_for_status()
                    data = r.json()
                    break
                except httpx.HTTPError as e:
                    if attempt == 1:
                        detail = getattr(getattr(e, "response", None), "text", "")[:200]
                        raise LLMError(f"{type(e).__name__}: {e} {detail}".strip()) from e
        finally:
            self.budget.release(need)
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


def _strip_fences(s: str) -> str:
    """Some servers wrap JSON-mode answers in ```json fences."""
    t = s.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()
