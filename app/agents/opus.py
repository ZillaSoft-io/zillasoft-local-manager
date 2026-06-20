"""Opus — thin identity. All pipeline skills (incl. implement_with_tools) live
on the base Agent so any model can perform any task. ImplementResult/OpusResult
are re-exported for compatibility."""
from __future__ import annotations

from .base import Agent, ImplementResult, OpusResult  # noqa: F401 re-export
from .prompts import IMPLEMENT_SYSTEM


class OpusAgent(Agent):
    label = "opus"
    system_prompt = IMPLEMENT_SYSTEM
    model_key = "ANTHROPIC_MODEL_OPUS"
    effort_key = "ANTHROPIC_EFFORT_OPUS"


__all__ = ["OpusAgent", "ImplementResult", "OpusResult"]
