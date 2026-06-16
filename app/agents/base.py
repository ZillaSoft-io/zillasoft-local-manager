"""Base class shared by the three role agents."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from .client import AgentResponse, AnthropicClient

logger = logging.getLogger(__name__)


class Agent:
    """Common plumbing: resolve model + effort from config, make calls."""

    #: subclasses set these
    label: str = "agent"
    system_prompt: str = ""
    model_key: str = ""    # e.g. "ANTHROPIC_MODEL_SONNET"
    effort_key: str = ""   # e.g. "ANTHROPIC_EFFORT_SONNET"

    def __init__(self, client: AnthropicClient, config):
        self.client = client
        self.config = config

    @property
    def model(self) -> str:
        return self.config.get_raw(self.model_key)

    @property
    def effort(self) -> Optional[str]:
        return self.config.get_raw(self.effort_key)

    def ask(self, user_content: str, *, max_tokens: int = 8000,
            thinking: bool = True, output_config: Optional[dict] = None,
            stream: bool = False) -> AgentResponse:
        """Single-turn call with this agent's system prompt, model, and effort."""
        return self.client.complete(
            model=self.model,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens,
            effort=self.effort,
            thinking=thinking,
            output_config=output_config,
            stream=stream,
            agent_label=self.label,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_json(text: str) -> Any:
        """Parse a JSON object from a response, tolerating code fences."""
        cleaned = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()
        return json.loads(cleaned)
