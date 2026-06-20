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
                 usage_tracker: Optional[UsageTracker] = None,
                 request_timeout: int = 120):
        self._config = config
        self._sdk = sdk_client          # injected (tests) or lazily built
        self.usage = usage_tracker or UsageTracker()
        self.request_timeout = request_timeout  # Stability 1: explicit timeout on all requests

    # ------------------------------------------------------------------ #
    # SDK client
    # ------------------------------------------------------------------ #
    def _client(self):
        if self._sdk is None:
            import anthropic  # imported lazily so the package loads without a key
            api_key = self._config.require("ANTHROPIC_API_KEY")
            # Stability 1: Set timeout on client + retry on transient failures
            self._sdk = anthropic.Anthropic(
                api_key=api_key,
                max_retries=3,
                timeout=self.request_timeout
            )
        return self._sdk

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #
    @staticmethod
    def _cached(block_text: str) -> list:
        """A system value as a single cache-breakpoint text block."""
        return [{"type": "text", "text": block_text,
                 "cache_control": {"type": "ephemeral"}}]

    @staticmethod
    def _with_cached_last_message(messages: list[dict]) -> list[dict]:
        """Shallow-copy `messages` with a cache breakpoint on the last block of
        the last message — incremental caching for long tool-use loops, so each
        step re-reads the prior turns from cache instead of reprocessing them."""
        if not messages:
            return messages
        msgs = list(messages)
        last = dict(msgs[-1])
        content = last.get("content")
        cc = {"cache_control": {"type": "ephemeral"}}
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content, **cc}]
        elif isinstance(content, list) and content and isinstance(content[-1], dict):
            new = list(content)
            new[-1] = {**content[-1], **cc}
            last["content"] = new
        else:
            return messages  # unknown shape — leave untouched
        msgs[-1] = last
        return msgs

    @staticmethod
    def _build_params(model: str, system: Optional[str],
                      messages: list[dict], max_tokens: int,
                      effort: Optional[str], thinking: bool,
                      output_config: Optional[dict],
                      tools: Optional[list] = None,
                      tool_choice: Optional[dict] = None,
                      cache_system: bool = False,
                      cache_messages: bool = False) -> dict:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            # Caching the system block also caches the (earlier) tools in the
            # prefix. Below the per-model minimum the API simply ignores the
            # marker, so this is always safe.
            params["system"] = (AnthropicClient._cached(system)
                                if cache_system else system)

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
        if cache_messages:
            params["messages"] = AnthropicClient._with_cached_last_message(messages)
        return params

    # ------------------------------------------------------------------ #
    # Calls
    # ------------------------------------------------------------------ #
    def complete(self, *, model: str, messages: list[dict],
                 system: Optional[str] = None, max_tokens: int = 8000,
                 effort: Optional[str] = None, thinking: bool = True,
                 output_config: Optional[dict] = None, stream: bool = False,
                 tools: Optional[list] = None, tool_choice: Optional[dict] = None,
                 agent_label: str = "agent",
                 cache_messages: bool = False) -> AgentResponse:
        """Make one model call and return the normalized response.

        Prompt caching (Anthropic ephemeral) is applied when
        LOCAL_MANAGER_PROMPT_CACHE is enabled: the system+tools prefix is always
        cached, and the message prefix is cached when `cache_messages` is set
        (used by the implementation tool-loop). Cache reads cost ~0.1x input.
        """
        cache_on = (self._config.get_raw(
            "LOCAL_MANAGER_PROMPT_CACHE", "true").lower() == "true")
        params = self._build_params(
            model, system, messages, max_tokens, effort, thinking,
            output_config, tools, tool_choice,
            cache_system=cache_on,
            cache_messages=(cache_on and cache_messages))
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

    # Optimization 3: Stream API for Opus (yield chunks as they arrive)
    def complete_streaming(self, *, model: str, messages: list[dict],
                          system: Optional[str] = None, max_tokens: int = 8000,
                          effort: Optional[str] = None, thinking: bool = True,
                          output_config: Optional[dict] = None,
                          tools: Optional[list] = None, tool_choice: Optional[dict] = None,
                          agent_label: str = "agent"):
        """Stream response chunks (generator). Yields text chunks as they arrive."""
        params = self._build_params(
            model, system, messages, max_tokens, effort, thinking,
            output_config, tools, tool_choice)
        sdk = self._client()
        try:
            with sdk.messages.stream(**params) as s:
                for text_event in s.text_stream:
                    yield text_event
                message = s.get_final_message()
        except Exception as exc:
            logger.error("Anthropic streaming call failed (%s): %s", model, exc)
            raise AgentError(f"Anthropic call failed for {model}: {exc}") from exc

        # Record usage after stream is complete
        usage = Usage.from_response(getattr(message, "usage", None))
        self.usage.record(agent_label, model, usage)

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
