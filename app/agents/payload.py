"""Inter-agent payload management (spec §1, "Context window management").

Agents pass only key outputs to each other, never full history, and each
payload must stay under 8,000 tokens. This module enforces that limit and
provides the prioritized-chunk fallback the spec describes (error first,
changed files second, reasoning last).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from ..errors import PayloadTooLargeError
from .tokens import TokenCounter, estimate_tokens

logger = logging.getLogger(__name__)

PAYLOAD_TOKEN_LIMIT = 8000


def enforce_payload_limit(
    text: str,
    *,
    limit: int = PAYLOAD_TOKEN_LIMIT,
    counter: Optional[TokenCounter] = None,
    reducer: Optional[Callable[[str, int], str]] = None,
) -> str:
    """Return `text` if within `limit`, else reduce it.

    `reducer(text, limit)` is an optional callback (e.g. ask Sonnet to
    re-summarize more tightly). If reduction still exceeds the limit, or no
    reducer is given, raise PayloadTooLargeError.
    """
    count = (counter.count if counter else estimate_tokens)
    tokens = count(text)
    if tokens <= limit:
        return text

    logger.warning("Payload ~%d tokens exceeds %d-token limit.", tokens, limit)
    if reducer is not None:
        reduced = reducer(text, limit)
        if count(reduced) <= limit:
            return reduced
        tokens = count(reduced)

    raise PayloadTooLargeError(tokens, limit)


@dataclass
class PrioritizedSummary:
    """The spec's prioritized-chunk shape for an over-budget Sonnet summary:
    error first, changed files second, reasoning last."""
    error: str = ""
    changed_files: str = ""
    reasoning: str = ""

    def render(self, limit: int = PAYLOAD_TOKEN_LIMIT) -> str:
        """Assemble in priority order, dropping lower-priority chunks until
        the result fits the token limit."""
        ordered = [
            ("ERROR", self.error),
            ("CHANGED FILES", self.changed_files),
            ("REASONING", self.reasoning),
        ]
        out: list[str] = []
        for label, chunk in ordered:
            if not chunk:
                continue
            candidate = out + [f"## {label}\n{chunk}"]
            if estimate_tokens("\n\n".join(candidate)) > limit:
                break
            out = candidate
        return "\n\n".join(out)
