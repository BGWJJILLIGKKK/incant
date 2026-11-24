"""Claude through the official Anthropic SDK: [llm] dialect = "anthropic" (extra: anthropic).

    [llm]
    dialect = "anthropic"
    model = "claude-opus-5-5"
    api_key_env = "ANTHROPIC_API_KEY"      # resolved from the environment or the data directory's .env
    context_tokens = 1000000
    concurrency = 8

Structured output: the response is constrained to the stage's pydantic schema (messages.parse). Thinking is
adaptive and always on for current models; [llm] effort / effort_thinking pick its depth for ordinary and
"think" calls. A declined request is re-run server-side on Anthropic's recommended fallback model
(fallbacks = "default"); a final refusal is an LLMError like any other failed window.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from .config import LLMConfig, secret

M = TypeVar("M", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MIN_MAX_TOKENS = 16000  # thinking counts against max_tokens: leave room for it


def request(cfg: LLMConfig, system: str, user: str, schema: type[BaseModel], think: bool, max_tokens: int) -> dict:
    """Everything that decides the answer (also the cache key)."""
    return {
        "model": cfg.model,
        "max_tokens": max(max_tokens, MIN_MAX_TOKENS),
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "effort": cfg.effort_thinking if think else cfg.effort,
        "schema": {"name": schema.__name__, "json": schema.model_json_schema()},
    }


def client(cfg: LLMConfig):
    try:
        import anthropic
    except ImportError:
        raise RuntimeError('[llm] dialect = "anthropic" needs the Anthropic SDK: uv sync --extra anthropic') from None
    key = secret(cfg.api_key_env) if cfg.api_key_env else None  # None: the SDK reads ANTHROPIC_API_KEY
    base = cfg.base_url if "anthropic" in cfg.base_url else None  # the llama.cpp default isn't meant for Claude
    return anthropic.Anthropic(api_key=key, base_url=base, timeout=cfg.timeout_s, max_retries=3)


def call(api, req: dict, schema: type[M]) -> tuple[M, dict]:
    """(validated answer, token usage). Raises LLMError on refusals, truncation and API errors."""
    import anthropic

    from .llm import LLMError

    try:
        r = api.beta.messages.parse(
            model=req["model"], max_tokens=req["max_tokens"], system=req["system"], messages=req["messages"],
            output_format=schema, output_config={"effort": req["effort"]},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Claude API connection error: {e}") from e
    usage = {"input": r.usage.input_tokens or 0, "output": r.usage.output_tokens or 0}
    if r.stop_reason == "refusal":
        cat = getattr(r.stop_details, "category", None) if r.stop_details else None
        raise LLMError(f"Claude declined the request (category: {cat})")
    if r.stop_reason == "max_tokens":
        raise LLMError(f"hit max_tokens={req['max_tokens']}")
    if r.parsed_output is None:
        raise LLMError(f"no {schema.__name__} in the response (stop_reason {r.stop_reason})")
    return r.parsed_output, usage
