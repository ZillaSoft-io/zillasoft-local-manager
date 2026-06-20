"""Sonnet — thin identity. All pipeline skills live on the base Agent so any
model can perform any task."""
from __future__ import annotations

from .base import Agent
from .prompts import PLAN_SYSTEM


class SonnetAgent(Agent):
    label = "sonnet"
    system_prompt = PLAN_SYSTEM
    model_key = "ANTHROPIC_MODEL_SONNET"
    effort_key = "ANTHROPIC_EFFORT_SONNET"


__all__ = ["SonnetAgent"]
