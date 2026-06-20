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
        # Capability ranking, cheapest -> most capable. Drives the fallback
        # ladder (escalate to a more capable model first, then fall back to
        # cheaper). A future model just slots into this list.
        self._capability_order = ["haiku", "sonnet", "opus"]
        # Configurable orchestration roles (swappable for future agents)
        self._validation_agent = "haiku"  # fast, cheap validation
        self._implementation_agent = "opus"  # powerful implementation
        self._planning_agent = "sonnet"  # balanced planning

    def get_capability_order(self) -> list[str]:
        """Capability ranking, cheapest -> most capable (drives fallback)."""
        return list(self._capability_order)

    def set_capability_order(self, order: list[str]) -> None:
        """Reorder/extend the capability ranking (e.g. when a model launches)."""
        if order:
            self._capability_order = list(order)

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

    def set_orchestration_roles(self, validation: str = None, implementation: str = None,
                                planning: str = None) -> None:
        """Configure which agents to use for orchestration roles.

        Allows swapping agents (e.g., when Mythos 5 launches) without code changes.

        Args:
            validation: agent label for plan validation (default: "haiku")
            implementation: agent label for implementation (default: "opus")
            planning: agent label for plan generation (default: "sonnet")
        """
        if validation:
            self.get(validation)  # Verify it exists
            self._validation_agent = validation
            logger.info(f"Set validation agent: {validation}")
        if implementation:
            self.get(implementation)
            self._implementation_agent = implementation
            logger.info(f"Set implementation agent: {implementation}")
        if planning:
            self.get(planning)
            self._planning_agent = planning
            logger.info(f"Set planning agent: {planning}")

    def get_validation_agent(self) -> str:
        """Get agent label for plan validation."""
        return self._validation_agent

    def get_implementation_agent(self) -> str:
        """Get agent label for implementation."""
        return self._implementation_agent

    def get_planning_agent(self) -> str:
        """Get agent label for plan generation."""
        return self._planning_agent


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


def register_default_agents() -> None:
    """Populate the global registry with the three standard agents.

    The registry backs the Settings -> Agents dropdowns and role selection.
    Actual inference prompts live in the agent classes; the system_prompt here
    is descriptive only. Called once at startup.
    """
    defaults = [
        AgentConfig("haiku", "ANTHROPIC_MODEL_HAIKU", "ANTHROPIC_EFFORT_HAIKU",
                    "Fast, cheap validation and review.", "cheap", False),
        AgentConfig("sonnet", "ANTHROPIC_MODEL_SONNET", "ANTHROPIC_EFFORT_SONNET",
                    "Balanced planning and analysis.", "medium", True),
        AgentConfig("opus", "ANTHROPIC_MODEL_OPUS", "ANTHROPIC_EFFORT_OPUS",
                    "Powerful implementation.", "expensive", True),
    ]
    for cfg in defaults:
        _REGISTRY.register(cfg)
