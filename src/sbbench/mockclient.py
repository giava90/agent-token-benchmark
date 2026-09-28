"""A fake Claude client for building and testing the harness at zero cost.

It is deliberately not a stub that returns constant usage. It reproduces the
cache rules that actually decide this study's numbers:

  * prefix match - the cache key is the exact bytes of the cached prefix
  * per-model minimum cacheable prefix - below it, nothing caches and NO error
    is raised (Haiku 4.5 needs 4096 tokens; this is the silent trap)
  * TTL, measured from request start, refreshed for free by a read
  * caches are model-scoped - a model switch can never hit another model's entry
  * an entry is readable only once the writing request has begun responding,
    so a naive parallel fan-out writes N entries and reads none

Swap `token_counter` for the real `messages.count_tokens` to get exact token
figures while still paying nothing (counting is free).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from . import pricing


def estimate_tokens(text: str) -> int:
    """Crude char/token ratio. Replace with count_tokens for real figures."""
    return max(1, len(text) // 4)


@dataclass
class CacheEntry:
    tokens: int
    written_at: float
    ttl_seconds: float
    visible: bool = True  # False while the writing request is still in flight


@dataclass
class MockUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


@dataclass
class MockResponse:
    text: str
    usage: MockUsage
    model: str
    stop_reason: str = "end_turn"


@dataclass
class MockAnthropic:
    """Simulated client. `clock` is a float seconds counter you advance yourself."""

    seed: int = 20260928
    accuracy: float = 0.85          # how often the fake model answers correctly
    output_tokens: int = 120
    token_counter: callable = estimate_tokens
    clock: float = 0.0
    cache: dict[str, CacheEntry] = field(default_factory=dict)
    calls: int = 0

    # ---- clock -------------------------------------------------------------
    def advance(self, seconds: float) -> None:
        self.clock += seconds

    # ---- cache -------------------------------------------------------------
    @staticmethod
    def _key(model: str, prefix: str) -> str:
        h = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
        return f"{model}:{h}"  # model-scoped: no cross-model reuse

    def _lookup(self, model: str, prefix: str) -> CacheEntry | None:
        entry = self.cache.get(self._key(model, prefix))
        if entry is None or not entry.visible:
            return None
        if self.clock - entry.written_at > entry.ttl_seconds:
            del self.cache[self._key(model, prefix)]
            return None
        return entry

    # ---- request -----------------------------------------------------------
    def create(
        self,
        model: str,
        cached_prefix: str = "",
        tail: str = "",
        ttl: str = "5m",
        use_cache: bool = True,
        answer: str | None = None,
        item_id: str = "",
        visible_immediately: bool = True,
        output_tokens: int | None = None,
    ) -> MockResponse:
        """One simulated Messages request.

        `cached_prefix` is everything up to and including the cache_control
        breakpoint; `tail` is the volatile remainder.
        """
        self.calls += 1
        price = pricing.get(model)
        prefix_tokens = self.token_counter(cached_prefix) if cached_prefix else 0
        tail_tokens = self.token_counter(tail) if tail else 0
        usage = MockUsage(
            output_tokens=self.output_tokens if output_tokens is None else output_tokens
        )

        cacheable = use_cache and prefix_tokens >= price.min_cacheable_prefix
        if not cacheable:
            # Silent: no error, the prefix is simply billed at full rate.
            usage.input_tokens = prefix_tokens + tail_tokens
        else:
            entry = self._lookup(model, cached_prefix)
            if entry is not None:
                usage.cache_read_input_tokens = entry.tokens
                usage.input_tokens = tail_tokens
                entry.written_at = self.clock  # a read refreshes, free of charge
            else:
                usage.cache_creation_input_tokens = prefix_tokens
                usage.input_tokens = tail_tokens
                self.cache[self._key(model, cached_prefix)] = CacheEntry(
                    tokens=prefix_tokens,
                    written_at=self.clock,
                    ttl_seconds=3600.0 if ttl == "1h" else 300.0,
                    visible=visible_immediately,
                )

        return MockResponse(text=answer if answer is not None else "", usage=usage, model=model)

    def reveal_pending(self) -> None:
        """Make in-flight cache writes readable (the first response has started)."""
        for e in self.cache.values():
            e.visible = True

    # ---- fake answers ------------------------------------------------------
    def verdict(self, item_id: str, truth: str, options: tuple[str, str] = ("supported", "not_supported")) -> str:
        """A deterministic wrong answer some of the time, to exercise grading."""
        rng = random.Random(f"{self.seed}:{item_id}")
        if rng.random() < self.accuracy:
            return truth
        return options[1] if truth == options[0] else options[0]
