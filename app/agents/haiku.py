"""Haiku — thin identity. All pipeline skills live on the base Agent so any
model can perform any task. Dataclasses are re-exported for compatibility."""
from __future__ import annotations

from .base import Agent, ClarifyTurn, ValidationVerdict  # noqa: F401 re-export
from .prompts import ORCHESTRATE_SYSTEM


class HaikuAgent(Agent):
    label = "haiku"
    system_prompt = ORCHESTRATE_SYSTEM
    model_key = "ANTHROPIC_MODEL_HAIKU"
    effort_key = "ANTHROPIC_EFFORT_HAIKU"


__all__ = ["HaikuAgent", "ValidationVerdict", "ClarifyTurn"]
