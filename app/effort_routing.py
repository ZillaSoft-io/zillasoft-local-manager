"""Intelligent effort routing: control model thinking depth based on task complexity.

Simple tasks → Low effort (faster, cheaper, less thinking)
Complex tasks → High effort (deeper reasoning, more comprehensive)
"""
from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class EffortLevel(str, Enum):
    """Model thinking effort levels."""
    LOW = "low"          # Simple tasks, straightforward logic
    NORMAL = "normal"    # Typical tasks, balanced thinking
    HIGH = "high"        # Complex tasks, deep reasoning needed


class EffortRouter:
    """Route tasks to appropriate thinking effort levels."""

    # Effort level multipliers (approximate token cost impact)
    EFFORT_MULTIPLIERS = {
        "low": 0.8,      # ~20% cheaper than normal
        "normal": 1.0,   # baseline
        "high": 1.5,     # ~50% more expensive, deeper thinking
    }

    @staticmethod
    def analyze_task_complexity(
        task_type: str,
        context_length: int = 0,
        has_failures: bool = False,
        change_complexity: str = "simple"
    ) -> EffortLevel:
        """Determine effort level based on task characteristics.

        Args:
            task_type: type of task (plan, implement, test_review, etc.)
            context_length: length of context in tokens
            has_failures: whether the task involves failures
            change_complexity: complexity of code changes (simple/complex)

        Returns:
            EffortLevel (LOW, NORMAL, or HIGH)
        """
        # Low effort: simple tasks
        if task_type == "validate_plan" and not has_failures:
            logger.debug("Simple plan validation → LOW effort")
            return EffortLevel.LOW

        if task_type == "test_review" and not has_failures:
            logger.debug("Simple test review (all pass) → LOW effort")
            return EffortLevel.LOW

        if change_complexity == "simple" and context_length < 5000:
            logger.debug(f"Simple change, low context → LOW effort")
            return EffortLevel.LOW

        # High effort: complex tasks
        if task_type == "plan" and context_length > 15000:
            logger.debug("Complex plan with large context → HIGH effort")
            return EffortLevel.HIGH

        if task_type == "test_review" and has_failures:
            logger.debug("Complex test failures → HIGH effort")
            return EffortLevel.HIGH

        if task_type == "implement" and change_complexity == "complex":
            logger.debug("Complex implementation → HIGH effort")
            return EffortLevel.HIGH

        # Normal effort: everything else
        logger.debug("Standard task → NORMAL effort")
        return EffortLevel.NORMAL

    @staticmethod
    def get_effort_config(effort_level: EffortLevel) -> dict:
        """Get API configuration for effort level.

        Returns dict with effort settings for agent calls.
        """
        config = {
            "effort_level": effort_level.value,
            "cost_multiplier": EffortRouter.EFFORT_MULTIPLIERS.get(effort_level.value, 1.0),
        }

        # Extended thinking token budgets (approximate)
        thinking_budgets = {
            "low": 2000,      # Minimal thinking
            "normal": 5000,   # Standard thinking
            "high": 15000,    # Deep reasoning
        }

        config["thinking_budget_tokens"] = thinking_budgets.get(effort_level.value, 5000)

        return config

    @staticmethod
    def apply_effort_to_call(agent, prompt: str, effort_level: EffortLevel,
                            agent_name: str = "unknown", **kwargs):
        """Apply effort level to an agent call.

        Only applies thinking budget to models that support it (Sonnet, Opus).
        Haiku does not support extended thinking.

        Args:
            agent: Agent instance
            prompt: prompt text
            effort_level: EffortLevel to use
            agent_name: name of agent ("haiku", "sonnet", "opus") for capability check
            **kwargs: additional arguments to pass to agent.invoke()

        Returns:
            Agent response
        """
        effort_config = EffortRouter.get_effort_config(effort_level)

        # Merge effort config with kwargs
        call_kwargs = {**kwargs}

        # Only Sonnet and Opus support extended thinking
        # Haiku does not support thinking_budget_tokens parameter
        if agent_name.lower() in ("sonnet", "opus"):
            call_kwargs["thinking_budget_tokens"] = effort_config["thinking_budget_tokens"]
            logger.debug(
                f"{agent_name} call with {effort_level.value} effort "
                f"(thinking budget: {effort_config['thinking_budget_tokens']} tokens)"
            )
        else:
            logger.debug(
                f"{agent_name} call with {effort_level.value} effort "
                f"(no thinking budget — {agent_name} does not support extended thinking)"
            )

        return agent.invoke(prompt, **call_kwargs)


# Global router instance
_router: EffortRouter | None = None


def get_effort_router() -> EffortRouter:
    """Get or create global effort router."""
    global _router
    if _router is None:
        _router = EffortRouter()
    return _router
