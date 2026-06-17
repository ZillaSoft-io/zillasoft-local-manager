"""Pre-run cost estimation: predict session cost before execution.

Estimates total cost based on task complexity, cycle count, and agents involved.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CostEstimator:
    """Estimate session costs before running."""

    # Cost per agent per operation (approximate)
    AGENT_COSTS = {
        "haiku": {
            "plan_validation": 0.0005,
            "test_review": 0.0008,
            "implement": 0.0010,
            "summarize": 0.0003,
        },
        "sonnet": {
            "plan_generation": 0.0015,
            "test_review": 0.0025,
            "implement": 0.0020,
            "bug_analysis": 0.0020,
            "summarize": 0.0008,
        },
        "opus": {
            "implement": 0.0080,
            "refine": 0.0060,
        }
    }

    @staticmethod
    def estimate_session(
        task_complexity: str = "medium",
        expected_cycles: int = 2,
        simple_change: bool = False
    ) -> dict:
        """Estimate total cost for a session.

        Args:
            task_complexity: "simple", "medium", or "complex"
            expected_cycles: expected number of test cycles
            simple_change: if True, assume simple code changes

        Returns:
            dict with cost_low, cost_high, breakdown
        """
        breakdown = {}
        total_low = 0.0
        total_high = 0.0

        # Haiku plan validation (always happens)
        breakdown["haiku_validation"] = CostEstimator.AGENT_COSTS["haiku"]["plan_validation"]
        total_low += breakdown["haiku_validation"]
        total_high += breakdown["haiku_validation"]

        # Plan generation (if complex)
        if task_complexity in ("medium", "complex"):
            breakdown["sonnet_planning"] = CostEstimator.AGENT_COSTS["sonnet"]["plan_generation"]
            total_low += breakdown["sonnet_planning"]
            total_high += breakdown["sonnet_planning"]

        # Implementation phase
        if task_complexity == "simple":
            # Simple tasks: Haiku implements
            breakdown["haiku_implementation"] = CostEstimator.AGENT_COSTS["haiku"]["implement"]
            total_low += breakdown["haiku_implementation"]
            total_high += breakdown["haiku_implementation"]
        else:
            # Complex tasks: Opus implements
            breakdown["opus_implementation"] = CostEstimator.AGENT_COSTS["opus"]["implement"]
            total_low += breakdown["opus_implementation"]
            total_high += breakdown["opus_implementation"] * 1.2  # variance for complex

        # Test execution and review (per cycle)
        test_cost_per_cycle = 0.0010  # tests themselves

        if simple_change and task_complexity == "simple":
            # Simple changes: Haiku runs and reviews tests
            test_agent_cost = CostEstimator.AGENT_COSTS["haiku"]["test_review"]
        elif task_complexity == "simple":
            # Simple task: Haiku reviews
            test_agent_cost = CostEstimator.AGENT_COSTS["haiku"]["test_review"]
        else:
            # Complex task: Sonnet reviews
            test_agent_cost = CostEstimator.AGENT_COSTS["sonnet"]["test_review"]

        cycle_cost = test_cost_per_cycle + test_agent_cost
        breakdown["test_cycles"] = cycle_cost * expected_cycles

        # Assume some cycles fail and retry
        if task_complexity == "complex":
            # Complex = more likely to fail, more cycles
            total_low += cycle_cost * min(expected_cycles, 2)
            total_high += cycle_cost * expected_cycles
        else:
            # Simple = likely to pass first try
            total_low += cycle_cost * 1
            total_high += cycle_cost * expected_cycles

        return {
            "cost_low": round(total_low, 4),
            "cost_high": round(total_high, 4),
            "cost_mid": round((total_low + total_high) / 2, 4),
            "breakdown": breakdown,
            "expected_cycles": expected_cycles,
            "complexity": task_complexity,
        }

    @staticmethod
    def format_estimate(estimate: dict) -> str:
        """Format estimate for display.

        Returns:
            Human-readable cost estimate string
        """
        return (
            f"💰 Estimated cost: ${estimate['cost_mid']:.4f} "
            f"(${estimate['cost_low']:.4f}-${estimate['cost_high']:.4f}) | "
            f"{estimate['expected_cycles']} cycles"
        )


# Global estimator
_estimator: CostEstimator | None = None


def get_cost_estimator() -> CostEstimator:
    """Get or create global cost estimator."""
    global _estimator
    if _estimator is None:
        _estimator = CostEstimator()
    return _estimator
