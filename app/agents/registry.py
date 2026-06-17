"""Agent registry for pluggable model management.

Decouples agent selection from hardcoded model names. Supports swapping agents
(including future models like Mythos 5) without code changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import AnthropicClient

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for a single agent role."""
    label: str                # "haiku", "sonnet", "opus", "mythos5", etc.
    model_key: str           # env var name, e.g. "ANTHROPIC_MODEL_HAIKU"
    effort_key: str          # env var name, e.g. "ANTHROPIC_EFFORT_HAIKU"
    system_prompt: str       # full system prompt
    cost_tier: str           # "cheap", "medium", "expensive" for cost budgeting
    supports_thinking: bool  # whether model supports adaptive thinking


class AgentRegistry:
    """Registry of available agents and their configurations.

    Central point for agent swapping. When Mythos 5 launches, register it here.
    """

    def __init__(self):
        self._agents: dict[str, AgentConfig] = {}
        self._primary_agent_order = ["haiku", "sonnet", "opus"]

    def register(self, config: AgentConfig) -> None:
        """Register an agent configuration."""
        self._agents[config.label] = config
        logger.debug(f"Registered agent: {config.label} (model_key={config.model_key})")

    def get(self, label: str) -> AgentConfig:
        """Get agent config by label (e.g., 'haiku', 'mythos5')."""
        if label not in self._agents:
            raise KeyError(f"Unknown agent: {label}. Registered: {list(self._agents.keys())}")
        return self._agents[label]

    def list_all(self) -> list[AgentConfig]:
        """List all registered agents in priority order."""
        result = []
        for label in self._primary_agent_order:
            if label in self._agents:
                result.append(self._agents[label])
        # Append any new agents not in primary order (e.g., future models)
        for label, config in self._agents.items():
            if label not in self._primary_agent_order:
                result.append(config)
        return result

    def get_by_tier(self, tier: str) -> AgentConfig:
        """Get cheapest agent matching a cost tier.

        Useful for selective routing: when a task is simple, get a "cheap" agent.
        """
        for config in self.list_all():
            if config.cost_tier == tier:
                return config
        raise ValueError(f"No agent found with cost_tier={tier}")


# Global registry instance
_REGISTRY = AgentRegistry()


def initialize_registry(registry: AgentRegistry) -> None:
    """Initialize the global registry with all configured agents.

    Call this once at startup after loading config but before creating agents.
    """
    global _REGISTRY
    _REGISTRY = registry


def get_registry() -> AgentRegistry:
    """Get the global agent registry."""
    return _REGISTRY


def register_agent(config: AgentConfig) -> None:
    """Convenience function to register an agent in the global registry."""
    _REGISTRY.register(config)
