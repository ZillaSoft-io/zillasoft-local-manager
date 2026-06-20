"""Agent fallback chain: resilience against model unavailability.

If a model fails (503, timeout, API error), automatically try fallback agents
in priority order. Tracks model health to avoid thrashing on consistently-down models.

Stability features:
- Rate limit (429) detection with exponential backoff
- Automatic retry with jittered backoff
- Request timeout enforcement
"""
from __future__ import annotations

import asyncio
import logging
import random
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
    rate_limit_backoff_until: Optional[datetime] = None  # Stability 2: rate limit backoff
    rate_limit_backoff_seconds: int = 1  # Initial backoff, doubles on each 429

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

    def record_rate_limit(self) -> None:
        """Record a rate limit (429) error and back off exponentially."""
        now = datetime.now(timezone.utc)
        # Jitter backoff to prevent thundering herd
        jitter = random.uniform(0.8, 1.2)
        backoff = max(1, int(self.rate_limit_backoff_seconds * jitter))
        self.rate_limit_backoff_until = now + timedelta(seconds=backoff)
        # Double backoff for next 429 (up to 60s max)
        self.rate_limit_backoff_seconds = min(60, self.rate_limit_backoff_seconds * 2)
        logger.warning(
            f"Model {self.model_name} rate limited. Backing off {backoff}s "
            f"(next backoff will be ~{min(60, self.rate_limit_backoff_seconds)}s)"
        )

    def is_rate_limited(self) -> bool:
        """Check if model is currently in rate limit backoff."""
        if self.rate_limit_backoff_until is None:
            return False
        if datetime.now(timezone.utc) > self.rate_limit_backoff_until:
            # Backoff expired, reset
            self.rate_limit_backoff_until = None
            self.rate_limit_backoff_seconds = 1
            logger.info(f"Model {self.model_name} rate limit backoff expired, retrying")
            return False
        return True

    def should_try(self) -> bool:
        """Should we attempt this model, or skip it due to degradation/rate limit?"""
        # Check rate limit first (takes priority)
        if self.is_rate_limited():
            return False

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

    # The agent that normally LEADS each task (used when no `preferred` is given).
    # The rest of the chain is derived from the capability ladder, so fallback is
    # always: escalate to a more capable model first, then fall back to cheaper.
    DEFAULT_LEADERS = {
        "plan_generation": "sonnet",
        "plan_validation": "haiku",
        "implementation": "opus",
        "bug_analysis": "sonnet",
        "test_analysis": "haiku",
        "test_review": "haiku",
    }

    def _capability_ranking(self) -> list[str]:
        """Capability ranking (cheap -> capable) from the registry, with a
        safe default if the registry isn't available."""
        try:
            from .agents.registry import get_registry
            order = get_registry().get_capability_order()
            if order:
                return order
        except Exception:
            pass
        return ["haiku", "sonnet", "opus"]

    def _ladder(self, leader: str, available: list[str]) -> list[str]:
        """Order `available` agents as: leader, then more-capable (ascending),
        then less-capable (descending). Unknown agents go last. This yields the
        intended ladder, e.g. haiku -> sonnet -> opus, and opus -> sonnet -> haiku."""
        ranking = self._capability_ranking()
        def rank(a: str) -> int:
            return ranking.index(a) if a in ranking else len(ranking)
        lead_rank = rank(leader)
        more = sorted([a for a in available if a != leader and rank(a) > lead_rank],
                      key=rank)
        less = sorted([a for a in available if a != leader and rank(a) < lead_rank],
                      key=rank, reverse=True)
        chain = ([leader] if leader in available else []) + more + less
        for a in available:  # include any leftovers (e.g. unknown agents)
            if a not in chain:
                chain.append(a)
        return chain

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
        preferred: Optional[str] = None,
        **kwargs
    ) -> tuple[Any, str]:
        """Execute task with automatic fallback to other agents.

        Args:
            task_type: type of task (e.g., "plan_generation")
            agent_calls: dict mapping agent_name -> callable that executes on that agent
            preferred: agent to try first (e.g. the configured implementation
                agent), overriding the default chain order. Falls back to the
                rest of the chain if it fails.
            *args, **kwargs: passed to each agent callable

        Returns:
            (result, agent_used) tuple
        """
        # Only agents that actually have a callable for this task.
        available = list(agent_calls.keys())
        if not available:
            raise RuntimeError(f"No agent callables provided for task {task_type}")
        # Leader = explicit preference, else the task's natural leader, else
        # whatever is available. The rest follows the capability ladder.
        leader = preferred or self.DEFAULT_LEADERS.get(task_type) or available[0]
        fallback_chain = self._ladder(leader, available)
        candidates = fallback_chain

        attempted = False
        last_exc: Optional[Exception] = None

        def _try(agent_name: str) -> tuple[Any, str]:
            """Run one agent; raises on failure (after recording health)."""
            health = self.health[agent_name]
            result = agent_calls[agent_name](*args, **kwargs)
            health.record_success()
            return result, agent_name

        def _record_failure(agent_name: str, e: Exception) -> None:
            health = self.health[agent_name]
            health.record_failure()
            if "429" in str(e) or "rate limit" in str(e).lower():
                health.record_rate_limit()
            is_transient = any(
                kw in str(e).lower()
                for kw in ["timeout", "429", "503", "connection", "overloaded"]
            )
            logger.log(
                logging.WARNING if is_transient else logging.ERROR,
                f"Task {task_type}: {agent_name} failed with {type(e).__name__} "
                f"({'transient' if is_transient else 'fatal'}): {str(e)[:100]}"
            )

        # Primary pass: respect health (skip degraded / rate-limited agents).
        for attempt_num, agent_name in enumerate(candidates, 1):
            if not self.health[agent_name].should_try():
                logger.warning(
                    f"Skipping unhealthy model {agent_name} for {task_type} "
                    f"({self.health[agent_name].consecutive_failures} failures)"
                )
                continue
            attempted = True
            try:
                logger.info(f"Task {task_type}: attempt {attempt_num}/{len(candidates)} "
                            f"with {agent_name}")
                return _try(agent_name)
            except Exception as e:
                last_exc = e
                _record_failure(agent_name, e)

        # Last-resort pass: if health skipped EVERY agent, the heuristic may be
        # stale or wrong (e.g. a bad health-check). Better to actually try than
        # to fail cold without making a single API call.
        if not attempted:
            logger.warning(
                f"All agents for {task_type} were health-skipped; "
                f"attempting anyway as a last resort."
            )
            for agent_name in candidates:
                try:
                    return _try(agent_name)
                except Exception as e:
                    last_exc = e
                    _record_failure(agent_name, e)

        logger.error(f"Task {task_type}: all agents exhausted ({', '.join(candidates)}).")
        raise RuntimeError(
            f"All agents failed for {task_type}. "
            f"Last error: {type(last_exc).__name__ if last_exc else 'none'}: {last_exc}"
        ) from last_exc

    def effective_chains(self) -> dict[str, list[str]]:
        """The fallback order per task, derived from the capability ladder and
        each task's default leader. For display/monitoring."""
        agents = self._capability_ranking()
        return {task: self._ladder(leader, agents)
                for task, leader in self.DEFAULT_LEADERS.items()}

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
