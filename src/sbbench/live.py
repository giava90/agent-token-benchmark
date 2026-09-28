"""The real Messages API client, shaped like the mock so arms can swap.

Two things here are model-specific and will 400 if mixed up:
  * `effort` lives inside output_config and is not available on Haiku 4.5
  * `budget_tokens` is removed on Opus 5 and Sonnet 5 - never send it

Thinking is adaptive by default on Opus 5 and Sonnet 5, and thinking tokens are
billed as output. That is the single largest unknown in the cost model, which is
what the canary exists to measure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .counting import client as make_client
from .pricing import get as get_price

NO_EFFORT_MODELS = {"claude-haiku-4-5"}


@dataclass
class LiveResponse:
    text: str
    usage: dict
    model: str
    stop_reason: str
    wall_seconds: float
    thinking_chars: int = 0


@dataclass
class LiveAnthropic:
    """Minimal wrapper. One call per item; caching handled by cache_control."""

    max_tokens: int = 2000
    effort: str | None = None
    _client: object = None
    calls: int = 0
    spend_guard_usd: float = 1.00
    _spent: float = field(default=0.0)

    def __post_init__(self):
        if self._client is None:
            self._client = make_client()

    def create(self, model: str, cached_prefix: str, tail: str,
               ttl: str = "5m", use_cache: bool = True) -> LiveResponse:
        system_block = {"type": "text", "text": cached_prefix}
        if use_cache:
            cc = {"type": "ephemeral"}
            if ttl == "1h":
                cc["ttl"] = "1h"
            # The breakpoint sits on the last block of the shared prefix, never
            # after the per-item tail - otherwise every request pays the write
            # premium on bytes that are never read back.
            system_block["cache_control"] = cc

        kwargs = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": [system_block],
            "messages": [{"role": "user", "content": tail}],
        }
        if self.effort and model not in NO_EFFORT_MODELS:
            kwargs["output_config"] = {"effort": self.effort}

        t0 = time.time()
        resp = self._client.messages.create(**kwargs)
        wall = time.time() - t0
        self.calls += 1

        text_parts, thinking_chars = [], 0
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "thinking":
                thinking_chars += len(getattr(block, "thinking", "") or "")

        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        }

        return LiveResponse(
            text="\n".join(text_parts).strip(),
            usage=usage,
            model=model,
            stop_reason=getattr(resp, "stop_reason", "") or "",
            wall_seconds=wall,
            thinking_chars=thinking_chars,
        )


def sanity_check_config(model: str, effort: str | None) -> list[str]:
    """Problems that would 400 or silently waste money. Empty list means clear."""
    problems = []
    price = get_price(model)
    if effort and model in NO_EFFORT_MODELS:
        problems.append(f"{model} does not accept an effort parameter")
    if effort and not price.supports_effort:
        problems.append(f"{model} is recorded as not supporting effort")
    return problems
