"""Model prices and cost accounting.

Verified against platform.claude.com/docs/en/about-claude/pricing on 2026-09-28.
Re-verify before publishing: prices change, and this table is the study's
headline metric.

USD per million tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    model: str
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    context: int
    min_cacheable_prefix: int   # shorter prefixes silently do not cache
    supports_effort: bool


PRICES: dict[str, Price] = {
    "claude-opus-5": Price(
        "claude-opus-5", 5.00, 25.00, 6.25, 10.00, 0.50, 1_000_000, 512, True
    ),
    "claude-sonnet-5": Price(
        "claude-sonnet-5", 2.00, 10.00, 2.50, 4.00, 0.20, 1_000_000, 1024, True
    ),
    "claude-haiku-4-5": Price(
        "claude-haiku-4-5", 1.00, 5.00, 1.25, 2.00, 0.10, 200_000, 4096, False
    ),
}

BATCH_DISCOUNT = 0.5  # applies to every token, cache reads and writes included


def get(model: str) -> Price:
    if model not in PRICES:
        raise KeyError(f"unknown model {model!r}; known: {sorted(PRICES)}")
    return PRICES[model]


def cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    ttl: str = "5m",
    batch: bool = False,
) -> float:
    """USD for one request.

    `input_tokens` is the uncached remainder only - the Messages API reports it
    that way, and adding it to the cache fields is how you get the true prompt
    size.
    """
    p = get(model)
    write_rate = p.cache_write_1h if ttl == "1h" else p.cache_write_5m
    total = (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_creation_tokens * write_rate
        + cache_read_tokens * p.cache_read
    ) / 1_000_000.0
    return total * (BATCH_DISCOUNT if batch else 1.0)


def cost_from_usage(model: str, usage: dict, ttl: str = "5m", batch: bool = False) -> float:
    """Cost from a Messages API `usage` object (or the mock client's)."""
    return cost(
        model,
        input_tokens=usage.get("input_tokens", 0) or 0,
        output_tokens=usage.get("output_tokens", 0) or 0,
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        ttl=ttl,
        batch=batch,
    )


def prompt_tokens(usage: dict) -> int:
    """Total prompt size. `input_tokens` alone is the uncached remainder."""
    return (
        (usage.get("input_tokens", 0) or 0)
        + (usage.get("cache_creation_input_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0)
    )


def cost_if_uncached(model: str, usage: dict, batch: bool = False) -> float:
    """What the same request would have cost with caching off.

    Caching is a billing transformation, not a behavioural one: the model sees
    byte-identical tokens either way, so the whole prompt simply re-prices at
    the full input rate. That makes the no-cache baseline a *derived* arm -
    exact arithmetic over a cached run's usage, not a sampled estimate - so it
    never has to be purchased.

    Validate the assumption once on a small subsample (measured vs derived);
    once it holds, it holds at every scale.
    """
    return cost(
        model,
        input_tokens=prompt_tokens(usage),
        output_tokens=usage.get("output_tokens", 0) or 0,
        batch=batch,
    )


def cache_savings(model: str, usage: dict, ttl: str = "5m", batch: bool = False) -> dict:
    """Actual vs uncached cost for one request, and the ratio between them."""
    actual = cost_from_usage(model, usage, ttl=ttl, batch=batch)
    uncached = cost_if_uncached(model, usage, batch=batch)
    return {
        "cost_usd": actual,
        "cost_usd_if_uncached": uncached,
        "saved_usd": uncached - actual,
        "ratio": (uncached / actual) if actual else float("nan"),
    }
