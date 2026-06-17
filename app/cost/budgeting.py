"""Cost budgeting and enforcement.

Tracks monthly spend against cap ($100/mo). Stops accepting new tasks
if near limit, prevents budget overruns.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BudgetStatus:
    """Current budget status."""
    monthly_cap: float
    current_spend: float
    remaining: float
    percent_used: float
    is_at_capacity: bool
    is_near_capacity: bool  # >80%
    next_reset_date: str


class BudgetManager:
    """Tracks monthly spend and enforces budget limits."""

    def __init__(self, monthly_cap: float = 100.0, current_spend: float = 0.0,
                 reset_month: Optional[str] = None):
        """
        Args:
            monthly_cap: monthly budget in USD (default $100)
            current_spend: already spent this month
            reset_month: YYYY-MM format (e.g. "2026-06")
        """
        self.monthly_cap = monthly_cap
        self.current_spend = current_spend
        self.reset_month = reset_month or self._current_month()
        self._thresholds_hit = set()

    @staticmethod
    def _current_month() -> str:
        """Current month in YYYY-MM format."""
        now = datetime.now(timezone.utc)
        return now.strftime("%Y-%m")

    def _next_month(self) -> str:
        """Next month in YYYY-MM format."""
        month = self.reset_month
        year, m = map(int, month.split("-"))
        if m == 12:
            return f"{year + 1}-01"
        return f"{year}-{m + 1:02d}"

    def _should_reset(self) -> bool:
        """Check if we've entered a new month."""
        return self.reset_month != self._current_month()

    def record_spend(self, amount_usd: float) -> float:
        """Record a spend and return new total.

        Returns:
            New total spend for the month
        """
        if self._should_reset():
            logger.info(f"Budget reset: {self.reset_month} -> {self._current_month()}")
            self.current_spend = 0.0
            self.reset_month = self._current_month()
            self._thresholds_hit = set()

        self.current_spend += amount_usd
        logger.debug(f"Spend recorded: ${amount_usd:.2f} (total: ${self.current_spend:.2f})")
        return self.current_spend

    def can_accept_task(self, estimated_cost: float = 2.0) -> bool:
        """Check if we can accept a new task.

        Args:
            estimated_cost: estimated cost of the task (default $2)

        Returns:
            True if accepting task won't exceed cap
        """
        projected = self.current_spend + estimated_cost
        can_accept = projected <= self.monthly_cap
        if not can_accept:
            logger.warning(
                f"Budget limit would be exceeded: ${projected:.2f} > ${self.monthly_cap:.2f}"
            )
        return can_accept

    def status(self) -> BudgetStatus:
        """Get current budget status."""
        if self._should_reset():
            self.current_spend = 0.0
            self.reset_month = self._current_month()
            self._thresholds_hit = set()

        remaining = self.monthly_cap - self.current_spend
        percent = (self.current_spend / self.monthly_cap * 100) if self.monthly_cap > 0 else 0

        return BudgetStatus(
            monthly_cap=self.monthly_cap,
            current_spend=self.current_spend,
            remaining=max(0.0, remaining),
            percent_used=round(percent, 1),
            is_at_capacity=self.current_spend >= self.monthly_cap,
            is_near_capacity=self.current_spend >= self.monthly_cap * 0.8,
            next_reset_date=self._next_month(),
        )

    def thresholds_crossed(self, before_percent: float,
                          after_percent: float) -> list[float]:
        """Detect which threshold percentages were crossed (50%, 75%, 90%).

        Args:
            before_percent: percent before this spend
            after_percent: percent after this spend

        Returns:
            List of thresholds crossed (e.g., [0.5, 0.75])
        """
        thresholds = [0.5, 0.75, 0.9]
        crossed = []

        for t in thresholds:
            if t not in self._thresholds_hit and before_percent < (t * 100) <= after_percent:
                crossed.append(t)
                self._thresholds_hit.add(t)

        return crossed

    def reset_for_test(self) -> None:
        """Reset budget for testing."""
        self.current_spend = 0.0
        self.reset_month = self._current_month()
        self._thresholds_hit = set()
