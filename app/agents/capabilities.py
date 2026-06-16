"""Per-model capability detection.

The spec configures an effort level for every agent, but not every model
accepts the parameters:
  * `output_config.effort` is supported on Opus 4.5+ and Sonnet 4.6, and 400s
    on Haiku 4.5 / Sonnet 4.5.
  * Adaptive thinking (`thinking: {type: "adaptive"}`) is supported on
    Fable 5 / Opus 4.6+ / Sonnet 4.6, and not on Haiku 4.5.

These helpers gate the request parameters so a configured effort of "medium"
for Haiku doesn't crash the call.  Matching is substring-based so it survives
bare aliases and (if ever used) date-suffixed IDs.
"""
from __future__ import annotations

# Models that accept output_config.effort.
_EFFORT_MODELS = (
    "opus-4-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6", "fable-5",
)
# Models that accept thinking: {type: "adaptive"}.
_ADAPTIVE_THINKING_MODELS = (
    "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6", "fable-5",
)
# Effort level "xhigh"/"max" are Opus-tier (4.6+) / Fable only; "max" also on
# Sonnet 4.6. We only ship low|medium|high in config, so no extra gating needed.

VALID_EFFORTS = frozenset({"low", "medium", "high", "max", "xhigh"})


def supports_effort(model: str) -> bool:
    return any(tag in model for tag in _EFFORT_MODELS)


def supports_adaptive_thinking(model: str) -> bool:
    return any(tag in model for tag in _ADAPTIVE_THINKING_MODELS)
