"""Token-usage accounting across agent calls.

Captures input/output/cache tokens per model. Cost calculation (and the
monthly cap enforcement) is layered on in Phase 4; this module only records
the raw usage the API returns so nothing is lost in the meantime.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class Usage:
    """Token usage from a single API response."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        """All input tokens, cached or not (what was actually processed)."""
        return (self.input_tokens
                + self.cache_creation_input_tokens
                + self.cache_read_input_tokens)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens),
        )

    @classmethod
    def from_response(cls, raw_usage) -> "Usage":
        """Build from an Anthropic response `.usage` object (attrs may be None)."""
        def g(name: str) -> int:
            return int(getattr(raw_usage, name, 0) or 0)
        return cls(
            input_tokens=g("input_tokens"),
            output_tokens=g("output_tokens"),
            cache_creation_input_tokens=g("cache_creation_input_tokens"),
            cache_read_input_tokens=g("cache_read_input_tokens"),
        )


@dataclass
class UsageTracker:
    """Thread-safe accumulator of usage keyed by agent label and model."""
    by_agent: dict[str, Usage] = field(default_factory=dict)
    by_model: dict[str, Usage] = field(default_factory=dict)
    total: Usage = field(default_factory=Usage)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, agent: str, model: str, usage: Usage) -> None:
        with self._lock:
            self.by_agent[agent] = self.by_agent.get(agent, Usage()) + usage
            self.by_model[model] = self.by_model.get(model, Usage()) + usage
            self.total = self.total + usage

    def reset(self) -> None:
        """Clear all accumulated usage. The agents (and their shared tracker) are
        cached across sessions, so the orchestrator calls this at the start of
        each session — otherwise every session's cost would be the cumulative
        total of all prior sessions."""
        with self._lock:
            self.by_agent = {}
            self.by_model = {}
            self.total = Usage()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "by_agent": {k: vars(v) for k, v in self.by_agent.items()},
                "by_model": {k: vars(v) for k, v in self.by_model.items()},
                "total": vars(self.total),
            }
