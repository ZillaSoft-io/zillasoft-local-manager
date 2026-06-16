"""Token counting for the inter-agent payload guard.

The authoritative way to count Claude tokens is the API's count_tokens
endpoint, but it needs a key + a network round-trip per check. For the 8k
inter-agent payload guard (an internal guardrail, not a user-facing cost
figure) a conservative local estimate is fine and keeps the hot path cheap.

`TokenCounter` uses the exact API when a live client is available and exact
counting is requested; otherwise it falls back to the estimate. This is NOT
tiktoken (which is OpenAI's tokenizer and wrong for Claude) — it's a plain
character-ratio heuristic tuned to over-count slightly so the guard errs safe.
"""
from __future__ import annotations

import math
from typing import Optional

# Conservative: English prose is ~4 chars/token; code/JSON is denser. Dividing
# by 3.5 over-estimates slightly, so the guard trips before the real limit.
_CHARS_PER_TOKEN = 3.5


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


class TokenCounter:
    """Counts tokens in a text blob, exact when possible else estimated."""

    def __init__(self, client: Optional["object"] = None, exact: bool = False):
        # `client` is an AnthropicClient (has .count_tokens). `exact` opts into
        # the API call; default False to stay offline-friendly and cheap.
        self._client = client
        self._exact = exact and client is not None

    def count(self, text: str, model: Optional[str] = None) -> int:
        if self._exact:
            try:
                return self._client.count_tokens(text, model=model)
            except Exception:
                pass  # fall back to estimate on any failure
        return estimate_tokens(text)
