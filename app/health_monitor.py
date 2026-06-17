"""Proactive health monitoring: periodic pings to verify agents are responsive.

Stability 3: Detect dead agents before sessions fail them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Periodically ping agents to detect unavailability."""

    def __init__(self, check_interval_seconds: int = 30):
        """Initialize health monitor.

        Args:
            check_interval_seconds: how often to ping agents
        """
        self.check_interval = check_interval_seconds
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.last_check_time: Optional[datetime] = None
        self.check_count = 0

    async def start(self, client: Any = None) -> None:
        """Start background health checks."""
        if self.running:
            return

        self.running = True
        logger.info(f"Health monitor started (check interval: {self.check_interval}s)")

        # Start background task
        self._task = asyncio.create_task(self._check_loop(client))

    async def stop(self) -> None:
        """Stop background health checks."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor stopped")

    async def _check_loop(self, client: Any) -> None:
        """Background loop: periodically check agent health."""
        while self.running:
            try:
                await self._check_agents(client)
                self.last_check_time = datetime.now(timezone.utc)
                self.check_count += 1
            except Exception as e:
                logger.error(f"Health check failed: {e}")

            # Wait before next check
            await asyncio.sleep(self.check_interval)

    async def _check_agents(self, client: Any) -> None:
        """Ping each agent with a lightweight test."""
        if not client:
            return

        from .agent_fallback import get_fallback_chain

        fallback = get_fallback_chain()

        for agent_name in ["haiku", "sonnet", "opus"]:
            try:
                # Lightweight token count request (not a full inference)
                # This tests API connectivity and auth without using quota
                model = {
                    "haiku": "claude-3-5-haiku-20241022",
                    "sonnet": "claude-3-5-sonnet-20241022",
                    "opus": "claude-opus-4-1-20250805",
                }.get(agent_name, "claude-3-5-sonnet-20241022")

                # This is a very cheap operation (no inference, just tokenization)
                token_count = client.count_tokens("health check", model=model)
                logger.debug(f"Health check {agent_name}: OK ({token_count} tokens)")

                # Mark as healthy if it responds
                if agent_name in fallback.health:
                    # Successful check resets consecutive failures
                    if fallback.health[agent_name].consecutive_failures > 0:
                        logger.info(f"Agent {agent_name} recovered (health check passed)")
                        fallback.health[agent_name].record_success()

            except Exception as e:
                logger.warning(f"Health check failed for {agent_name}: {type(e).__name__}: {str(e)[:100]}")

                # Mark as failure (fallback chain will track and degrade)
                if agent_name in fallback.health:
                    fallback.health[agent_name].record_failure()
                    if fallback.health[agent_name].consecutive_failures >= 2:
                        logger.error(f"Agent {agent_name} marked DEGRADED (failed {fallback.health[agent_name].consecutive_failures} checks)")


# Global health monitor
_monitor: HealthMonitor | None = None


def get_health_monitor() -> HealthMonitor:
    """Get or create global health monitor."""
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor


async def start_health_monitoring(client: Any) -> None:
    """Start the global health monitor (called at app startup)."""
    monitor = get_health_monitor()
    await monitor.start(client)


async def stop_health_monitoring() -> None:
    """Stop the global health monitor (called at app shutdown)."""
    monitor = get_health_monitor()
    await monitor.stop()
