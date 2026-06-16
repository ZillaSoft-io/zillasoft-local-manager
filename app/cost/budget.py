"""Monthly cost cap — enforcement, spend accounting, and auto-reset (spec §4.1).

All state lives in config (.env): LOCAL_MANAGER_MONTHLY_COST_CAP,
LOCAL_MANAGER_CURRENT_MONTH_SPENT, LOCAL_MANAGER_COST_RESET_MONTH. These are
manager settings (agent-writable, not credentials).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_WARN_THRESHOLDS = (0.5, 0.8, 1.0)


class MonthlyBudget:
    def __init__(self, config):
        self._config = config

    @property
    def cap(self) -> float:
        return float(self._config.get("LOCAL_MANAGER_MONTHLY_COST_CAP", 0.0) or 0.0)

    @property
    def spent(self) -> float:
        return float(
            self._config.get("LOCAL_MANAGER_CURRENT_MONTH_SPENT", 0.0) or 0.0)

    @property
    def remaining(self) -> float:
        return self.cap - self.spent

    @property
    def fraction(self) -> float:
        cap = self.cap
        return (self.spent / cap) if cap > 0 else 0.0

    # ------------------------------------------------------------------ #
    def record_spend(self, amount: float) -> float:
        """Add `amount` to this month's spend and return the new total."""
        new_total = round(self.spent + max(0.0, amount), 4)
        self._config.set("LOCAL_MANAGER_CURRENT_MONTH_SPENT", new_total,
                         actor="agent")
        return new_total

    def would_exceed(self, estimated_cost: float,
                     scope_level: str = "capped") -> bool:
        """True if a session estimated at `estimated_cost` would blow the cap.
        Uncapped sessions never exceed."""
        if scope_level == "uncapped" or self.cap <= 0:
            return False
        return (self.spent + estimated_cost) > self.cap

    def thresholds_crossed(self, old_spent: float, new_spent: float) -> list[float]:
        """Which of 50/80/100% the spend crossed moving old→new (for warnings)."""
        cap = self.cap
        if cap <= 0:
            return []
        return [t for t in _WARN_THRESHOLDS if old_spent < t * cap <= new_spent]

    # ------------------------------------------------------------------ #
    def maybe_reset(self, now: datetime | None = None) -> bool:
        """Reset the monthly spend if the stored reset-month differs from now.
        Called on every startup so it self-heals even after weeks idle."""
        now = now or datetime.now(timezone.utc)
        current = now.strftime("%Y-%m")
        stored = self._config.get_raw("LOCAL_MANAGER_COST_RESET_MONTH", "")
        if stored == current:
            return False
        self._config.set("LOCAL_MANAGER_CURRENT_MONTH_SPENT", 0.0, actor="agent")
        self._config.set("LOCAL_MANAGER_COST_RESET_MONTH", current, actor="agent")
        logger.info("Monthly spend reset for %s (was %s).", current, stored)
        return True

    def snapshot(self) -> dict:
        return {
            "cap": self.cap,
            "spent": self.spent,
            "remaining": self.remaining,
            "fraction": round(self.fraction, 4),
            "reset_month": self._config.get_raw("LOCAL_MANAGER_COST_RESET_MONTH"),
        }
