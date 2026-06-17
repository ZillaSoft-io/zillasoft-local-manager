"""Detailed cost breakdown per agent and cycle.

Tracks token usage and cost per agent, enabling cost visibility and
optimization (e.g., "Sonnet is overspending, route more work to Haiku").
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from ..agents.usage import Usage, UsageTracker
from .pricing import cost_for

logger = logging.getLogger(__name__)


@dataclass
class AgentCost:
    """Cost breakdown for one agent in one cycle."""
    agent_label: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    @classmethod
    def from_usage(cls, agent_label: str, model: str, usage: Usage) -> AgentCost:
        """Build from Usage record and calculate cost."""
        total = (usage.input_tokens + usage.output_tokens
                 + usage.cache_creation_input_tokens + usage.cache_read_input_tokens)
        cost = cost_for(model, usage)
        return cls(
            agent_label=agent_label,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=usage.cache_creation_input_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            total_tokens=total,
            cost_usd=cost,
        )


@dataclass
class CycleBreakdown:
    """Complete cost breakdown for one cycle (Haiku → Sonnet → Opus)."""
    cycle_num: int
    agents: list[AgentCost] = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_tokens: int = 0

    def add_agent(self, agent_cost: AgentCost) -> None:
        """Record cost for one agent."""
        self.agents.append(agent_cost)
        self.total_cost_usd += agent_cost.cost_usd
        self.total_tokens += agent_cost.total_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "cycle_num": self.cycle_num,
            "agents": [asdict(ac) for ac in self.agents],
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CycleBreakdown:
        """Deserialize from JSON-compatible dict."""
        breakdown = cls(cycle_num=data["cycle_num"])
        for agent_data in data.get("agents", []):
            breakdown.agents.append(AgentCost(**agent_data))
        breakdown.total_cost_usd = data.get("total_cost_usd", 0.0)
        breakdown.total_tokens = data.get("total_tokens", 0)
        return breakdown


@dataclass
class SessionCostBreakdown:
    """Cost breakdown for an entire session (all cycles)."""
    session_id: str
    cycles: list[CycleBreakdown] = field(default_factory=list)

    def add_cycle(self, cycle: CycleBreakdown) -> None:
        """Record cost for one cycle."""
        self.cycles.append(cycle)

    @property
    def total_cost_usd(self) -> float:
        """Sum of all cycles."""
        return sum(c.total_cost_usd for c in self.cycles)

    @property
    def total_tokens(self) -> int:
        """Sum of all cycles."""
        return sum(c.total_tokens for c in self.cycles)

    @property
    def agent_summary(self) -> dict[str, dict[str, Any]]:
        """Aggregate costs by agent across all cycles."""
        summary: dict[str, dict[str, Any]] = {}
        for cycle in self.cycles:
            for agent_cost in cycle.agents:
                label = agent_cost.agent_label
                if label not in summary:
                    summary[label] = {
                        "agent": label,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                        "call_count": 0,
                    }
                summary[label]["total_tokens"] += agent_cost.total_tokens
                summary[label]["cost_usd"] += agent_cost.cost_usd
                summary[label]["call_count"] += 1
        return summary

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "session_id": self.session_id,
            "cycles": [c.to_dict() for c in self.cycles],
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "agent_summary": self.agent_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionCostBreakdown:
        """Deserialize from JSON-compatible dict."""
        breakdown = cls(session_id=data["session_id"])
        for cycle_data in data.get("cycles", []):
            breakdown.add_cycle(CycleBreakdown.from_dict(cycle_data))
        return breakdown


def build_cycle_breakdown(cycle_num: int, tracker: UsageTracker,
                          agent_models: dict[str, str]) -> CycleBreakdown:
    """Build a CycleBreakdown from a UsageTracker and agent model mapping.

    Args:
        cycle_num: cycle number (1, 2, 3, ...)
        tracker: UsageTracker with recorded usage
        agent_models: dict mapping agent label to model ID
                      (e.g., {"haiku": "claude-haiku-4-5", ...})

    Returns:
        CycleBreakdown with per-agent costs.
    """
    breakdown = CycleBreakdown(cycle_num=cycle_num)
    for agent_label, usage in tracker.by_agent.items():
        model = agent_models.get(agent_label, "unknown")
        agent_cost = AgentCost.from_usage(agent_label, model, usage)
        breakdown.add_agent(agent_cost)
        logger.debug(
            f"Cycle {cycle_num} - {agent_label}: {agent_cost.total_tokens} tokens, "
            f"${agent_cost.cost_usd:.4f}"
        )
    return breakdown
