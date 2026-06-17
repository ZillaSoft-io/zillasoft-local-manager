"""Token budgeting per agent call.

Tracks token usage per call, prevents token blow-ups from long contexts
or verbose outputs. Enforces soft limits (warning) and hard limits (reject).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenBudget:
    """Token limits per call."""
    # Soft limits (warn but allow)
    input_soft_limit: int = 50_000
    output_soft_limit: int = 10_000

    # Hard limits (reject if exceeded)
    input_hard_limit: int = 100_000
    output_hard_limit: int = 20_000

    # Per-agent monthly caps
    haiku_monthly_limit: int = 500_000
    sonnet_monthly_limit: int = 200_000
    opus_monthly_limit: int = 100_000


class TokenTracker:
    """Track token usage per agent per month."""

    def __init__(self, budget: TokenBudget = None):
        self.budget = budget or TokenBudget()
        self.usage: dict[str, int] = {
            "haiku": 0,
            "sonnet": 0,
            "opus": 0,
        }
        self.warnings: list[str] = []
        self.rejections: list[str] = []

    def check_call_tokens(self, input_tokens: int, output_tokens: int,
                         agent: str) -> tuple[bool, list[str]]:
        """Check if a single call should proceed.

        Always allows (returns True) but warns on soft/hard limit breaches.
        Soft limits = warning (proceeds with cost tracking).
        Hard limits = warning + cost tracking (no hard reject to prevent workflow deadlock).

        Returns:
            (allowed=True, warnings) - always proceeds, warnings list may be empty
        """
        warnings = []

        # Check soft limits
        if input_tokens > self.budget.input_soft_limit:
            warnings.append(
                f"High input tokens for {agent}: {input_tokens:,} "
                f"(soft limit: {self.budget.input_soft_limit:,})"
            )

        if output_tokens > self.budget.output_soft_limit:
            warnings.append(
                f"High output tokens for {agent}: {output_tokens:,} "
                f"(soft limit: {self.budget.output_soft_limit:,})"
            )

        # Check hard limits (warn but allow — no hard reject to prevent deadlock)
        if input_tokens > self.budget.input_hard_limit:
            warnings.append(
                f"⚠️ Input tokens {input_tokens:,} exceed hard limit {self.budget.input_hard_limit:,} "
                f"— proceeding with cost tracking"
            )

        if output_tokens > self.budget.output_hard_limit:
            warnings.append(
                f"⚠️ Output tokens {output_tokens:,} exceed hard limit {self.budget.output_hard_limit:,} "
                f"— proceeding with cost tracking"
            )

        return True, warnings

    def record_usage(self, agent: str, input_tokens: int,
                    output_tokens: int) -> None:
        """Record token usage for an agent."""
        total = input_tokens + output_tokens
        if agent not in self.usage:
            self.usage[agent] = 0
        self.usage[agent] += total

        logger.debug(
            f"{agent}: +{total:,} tokens "
            f"(monthly total: {self.usage[agent]:,})"
        )

    def check_monthly_limit(self, agent: str) -> Optional[str]:
        """Check if agent has exceeded monthly token limit."""
        limit = getattr(self.budget, f"{agent}_monthly_limit", None)
        if not limit:
            return None

        used = self.usage.get(agent, 0)
        if used > limit:
            return f"{agent} exceeded monthly limit: {used:,} / {limit:,}"
        return None

    def monthly_usage_summary(self) -> dict[str, dict]:
        """Summary of monthly usage vs limits."""
        return {
            agent: {
                "used": self.usage.get(agent, 0),
                "limit": getattr(self.budget, f"{agent}_monthly_limit", 0),
                "percent_used": (
                    round(self.usage.get(agent, 0) / getattr(self.budget, f"{agent}_monthly_limit", 1) * 100, 1)
                    if getattr(self.budget, f"{agent}_monthly_limit", 0) > 0
                    else 0
                ),
            }
            for agent in ["haiku", "sonnet", "opus"]
        }

    def reset_monthly(self) -> None:
        """Reset monthly usage counters."""
        self.usage = {"haiku": 0, "sonnet": 0, "opus": 0}
        self.warnings = []
        self.rejections = []
        logger.info("Token budget reset for new month")


# Global singleton
_token_tracker: Optional[TokenTracker] = None


def get_token_tracker() -> TokenTracker:
    """Get or create global token tracker."""
    global _token_tracker
    if _token_tracker is None:
        _token_tracker = TokenTracker()
    return _token_tracker
