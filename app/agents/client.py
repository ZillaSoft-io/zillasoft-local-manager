"""Anthropic SDK wrapper — one call path for all three models.

Responsibilities:
  * Build the request with per-model capability gating (effort, adaptive
    thinking) so a configured effort for Haiku doesn't 400.
  * Never send removed sampling params (temperature/top_p/top_k) or
    budget_tokens — all 400 on Opus 4.8 / Sonnet 4.6.
  * Capture token usage from every response into the shared UsageTracker.
  * Support streaming for long outputs (via .get_final_message()).
  * Map SDK exceptions to a clean AgentError.

The underlying `anthropic.Anthropic` client can be injected (for tests) or
built lazily from config on first use.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..errors import AgentError
from .capabilities import supports_adaptive_thinking, supports_effort
from .usage import Usage, UsageTracker

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    """Normalized result of one model call."""
    text: str
    usage: Usage
    model: str
    stop_reason: Optional[str] = None
    raw: Any = field(default=None, repr=False)


class AnthropicClient:
    """Thin, capability-aware wrapper over `anthropic.Anthropic`."""

    def __init__(self, config, sdk_client: Any = None,
                 usage_tracker: Optional[UsageTracker] = None):
        self._config = config
        self._sdk = sdk_client          # injected (tests) or lazily built
        self.usage = usage_tracker or UsageTracker()

    # ------------------------------------------------------------------ #
    # SDK client
    # ------------------------------------------------------------------ #
    def _client(self):
        if self._sdk is None:
            import anthropic  # imported lazily so the package loads without a key
            api_key = self._config.require("ANTHROPIC_API_KEY")
            self._sdk = anthropic.Anthropic(api_key=api_key, max_retries=3)
        return self._sdk

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_params(model: str, system: Optional[str],
                      messages: list[dict], max_tokens: int,
                      effort: Optional[str], thinking: bool,
                      output_config: Optional[dict],
                      tools: Optional[list] = None,
                      tool_choice: Optional[dict] = None) -> dict:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            params["system"] = system

        if thinking and supports_adaptive_thinking(model):
            params["thinking"] = {"type": "adaptive"}

        oc: dict[str, Any] = dict(output_config) if output_config else {}
        if effort and supports_effort(model):
            oc["effort"] = effort
        if oc:
            params["output_config"] = oc

        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        return params

    # ------------------------------------------------------------------ #
    # Calls
    # ------------------------------------------------------------------ #
    def complete(self, *, model: str, messages: list[dict],
                 system: Optional[str] = None, max_tokens: int = 8000,
                 effort: Optional[str] = None, thinking: bool = True,
                 output_config: Optional[dict] = None, stream: bool = False,
                 tools: Optional[list] = None, tool_choice: Optional[dict] = None,
                 agent_label: str = "agent") -> AgentResponse:
        """Make one model call and return the normalized response."""
        params = self._build_params(
            model, system, messages, max_tokens, effort, thinking,
            output_config, tools, tool_choice)
        sdk = self._client()
        try:
            if stream:
                with sdk.messages.stream(**params) as s:
                    message = s.get_final_message()
            else:
                message = sdk.messages.create(**params)
        except Exception as exc:  # normalize SDK + network errors
            logger.error("Anthropic call failed (%s): %s", model, exc)
            raise AgentError(f"Anthropic call failed for {model}: {exc}") from exc

        text = self._extract_text(message)
        usage = Usage.from_response(getattr(message, "usage", None))
        self.usage.record(agent_label, model, usage)
        return AgentResponse(
            text=text,
            usage=usage,
            model=model,
            stop_reason=getattr(message, "stop_reason", None),
            raw=message,
        )

    @staticmethod
    def _extract_text(message: Any) -> str:
        """Concatenate text blocks from a response, ignoring thinking blocks."""
        parts: list[str] = []
        for block in getattr(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts).strip()

    # ------------------------------------------------------------------ #
    # Token counting (exact, via the API)
    # ------------------------------------------------------------------ #
    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        model = model or self._config.get_raw("ANTHROPIC_MODEL_SONNET",
                                              "claude-sonnet-4-6")
        sdk = self._client()
        resp = sdk.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}])
        return int(resp.input_tokens)
