"""Per-model token pricing and cost calculation.

Rates are USD per 1M tokens (input, output), matching the current Anthropic
pricing — note Haiku 4.5 is $1/$5, NOT the spec's stale $0.8/$4 (that was
Haiku 3.5). Cache tokens are billed off the input rate: writes ~1.25x, reads
~0.1x.
"""
from __future__ import annotations

from ..agents.usage import Usage

# model-name substring -> (input $/1M, output $/1M)
PRICING = {
    "haiku": (1.0, 5.0),
    "sonnet": (3.0, 15.0),
    "opus": (5.0, 25.0),
    "fable": (10.0, 50.0),
}

_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


def _rates(model: str) -> tuple[float, float]:
    for tag, rates in PRICING.items():
        if tag in model:
            return rates
    # Unknown model — price at Opus (conservative, avoids under-counting).
    return PRICING["opus"]


def cost_for(model: str, usage: Usage) -> float:
    """USD cost for one usage record on `model`."""
    in_rate, out_rate = _rates(model)
    dollars = (
        usage.input_tokens * in_rate
        + usage.cache_creation_input_tokens * in_rate * _CACHE_WRITE_MULT
        + usage.cache_read_input_tokens * in_rate * _CACHE_READ_MULT
        + usage.output_tokens * out_rate
    ) / 1_000_000
    return round(dollars, 6)
