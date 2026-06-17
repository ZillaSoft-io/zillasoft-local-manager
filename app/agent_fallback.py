"""Agent fallback chain: resilience against model unavailability.

If a model fails (503, timeout, API error), automatically try fallback agents
in priority order. Tracks model health to avoid thrashing on consistently-down models.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelHealth:
    """Health status of a model."""
    model_name: str
    consecutive_failures: int = 0
    last_failure_time: Optional[datetime] = None
    is_degraded: bool = False
    degraded_at: Optional[datetime] = None
    recovery_attempted_at: Optional[datetime] = None

    def record_failure(self) -> None:
        """Record a failure."""
        self.consecutive_failures += 1
        self.last_failure_time = datetime.now(timezone.utc)
        if self.consecutive_failures >= 2 and not self.is_degraded:
            self.is_degraded = True
            self.degraded_at = datetime.now(timezone.utc)
            logger.warning(f"Model {self.model_name} marked as DEGRADED after {self.consecutive_failures} failures")

    def record_success(self) -> None:
        """Record a successful call."""
        if self.consecutive_failures > 0:
            logger.info(f"Model {self.model_name} recovered (was: {self.consecutive_failures} failures)")
        self.consecutive_failures = 0
        self.is_degraded = False
        self.last_failure_time = None

    def should_try(self) -> bool:
        """Should we attempt this model, or skip it due to degradation?"""
        if not self.is_degraded:
            return True

        # If degraded, try to recover after 60s
        if self.degraded_at and datetime.now(timezone.utc) - self.degraded_at > timedelta(seconds=60):
            self.recovery_attempted_at = datetime.now(timezone.utc)
            logger.info(f"Attempting recovery of degraded model {self.model_name}")
            return True

        return False


class AgentFallbackChain:
    """Manages fallback chains and model health tracking."""

    # Define fallback chains per task type
    FALLBACK_CHAINS = {
        "plan_generation": ["sonnet", "haiku", "opus"],
        "plan_validation": ["haiku", "sonnet", "opus"],
        "implementation": ["opus", "sonnet", "haiku"],
        "bug_analysis": ["sonnet", "opus", "haiku"],
        "test_analysis": ["haiku", "sonnet", "opus"],
        "test_review": ["haiku", "sonnet", "opus"],
    }

    def __init__(self):
        self.health: dict[str, ModelHealth] = {
            "haiku": ModelHealth("haiku"),
            "sonnet": ModelHealth("sonnet"),
            "opus": ModelHealth("opus"),
        }

    def execute_with_fallback(
        self,
        task_type: str,
        agent_calls: dict[str, Callable],
        *args,
        **kwargs
    ) -> tuple[Any, str]:
        """Execute task with automatic fallback to other agents.

        Args:
            task_type: type of task (e.g., "plan_generation")
            agent_calls: dict mapping agent_name -> callable that executes on that agent
            *args, **kwargs: passed to each agent callable

        Returns:
            (result, agent_used) tuple
        """
        fallback_chain = self.FALLBACK_CHAINS.get(task_type, ["sonnet", "haiku", "opus"])

        for attempt_num, agent_name in enumerate(fallback_chain, 1):
            if agent_name not in agent_calls:
                logger.warning(f"Agent {agent_name} not available for task {task_type}")
                continue

            # Check health before attempting
            health = self.health[agent_name]
            if not health.should_try():
                logger.warning(
                    f"Skipping degraded model {agent_name} for {task_type} "
                    f"({health.consecutive_failures} failures)"
                )
                continue

            try:
                logger.info(
                    f"Task {task_type}: attempt {attempt_num}/{len(fallback_chain)} "
                    f"with {agent_name}"
                )
                result = agent_calls[agent_name](*args, **kwargs)
                health.record_success()
                return result, agent_name

            except Exception as e:
                error_type = type(e).__name__
                health.record_failure()

                # Determine if error is transient or fatal
                is_transient = any(
                    keyword in str(e).lower()
                    for keyword in ["timeout", "429", "503", "connection", "overloaded"]
                )

                log_level = logging.WARNING if is_transient else logging.ERROR
                logger.log(
                    log_level,
                    f"Task {task_type}: {agent_name} failed with {error_type} "
                    f"({'transient' if is_transient else 'fatal'}): {str(e)[:100]}"
                )

                if attempt_num < len(fallback_chain):
                    logger.info(f"Trying fallback agent...")
                    continue
                else:
                    # All agents exhausted
                    logger.error(
                        f"Task {task_type}: All agents exhausted. "
                        f"Failed agents: {', '.join(fallback_chain)}"
                    )
                    raise RuntimeError(
                        f"All agents failed for {task_type}. Last error: {error_type}: {str(e)}"
                    ) from e

        # Shouldn't reach here, but just in case
        raise RuntimeError(f"No available agents for task {task_type}")

    def get_health_summary(self) -> dict[str, Any]:
        """Get summary of model health for monitoring."""
        return {
            agent_name: {
                "consecutive_failures": h.consecutive_failures,
                "is_degraded": h.is_degraded,
                "last_failure": h.last_failure_time.isoformat() if h.last_failure_time else None,
                "degraded_since": h.degraded_at.isoformat() if h.degraded_at else None,
            }
            for agent_name, h in self.health.items()
        }

    def reset_health(self, agent_name: str) -> None:
        """Manually reset health status (for debugging/ops)."""
        if agent_name in self.health:
            self.health[agent_name].record_success()
            logger.info(f"Reset health for {agent_name}")


# Global fallback chain manager
_fallback_chain: AgentFallbackChain | None = None


def get_fallback_chain() -> AgentFallbackChain:
    """Get or create global fallback chain manager."""
    global _fallback_chain
    if _fallback_chain is None:
        _fallback_chain = AgentFallbackChain()
    return _fallback_chain
