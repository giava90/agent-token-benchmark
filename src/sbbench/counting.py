"""Exact token counts via the free count_tokens endpoint.

Token counting is free to use (platform docs, "Pricing and rate limits") and runs
on a rate-limit pool separate from message creation, so this module costs nothing
to run.

It counts under the tokenizer of the model you pass, which is the whole point:
Claude 4.7+ models tokenize the same text about 30% higher than earlier ones, so
a count taken on one model must never be reused for another.

The endpoint ignores caching - cache_control is accepted but nothing is cached -
so it reports the *uncached* size of a prompt. Cache economics are applied on top
of these counts, in pricing.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional
    pass


def client():
    """The SDK resolves credentials itself; let it, and surface failures upstream."""
    import anthropic

    return anthropic.Anthropic()


def explain_auth_error(exc: Exception) -> str:
    return (
        f"{type(exc).__name__}: {exc}\n\n"
        "Token counting is free but still authenticates. Check that a file named\n"
        ".env exists in the project root and defines ANTHROPIC_API_KEY.\n"
        "Create a key at https://platform.claude.com -> Settings -> API keys."
    )


@dataclass
class Counter:
    """Counts tokens for one model, memoised within the process."""

    model: str
    _client: object = None
    _cache: dict = field(default_factory=dict)
    calls: int = 0

    def __post_init__(self):
        if self._client is None:
            self._client = client()

    def count(self, text: str, system: str | None = None) -> int:
        key = (system or "", text)
        if key in self._cache:
            return self._cache[key]
        kwargs = {"model": self.model, "messages": [{"role": "user", "content": text}]}
        if system:
            kwargs["system"] = system
        resp = self._client.messages.count_tokens(**kwargs)
        self.calls += 1
        self._cache[key] = resp.input_tokens
        return resp.input_tokens

    def as_token_counter(self):
        """A callable matching mockclient.estimate_tokens, for exact mock runs."""
        return lambda text: self.count(text)
