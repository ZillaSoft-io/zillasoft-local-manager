"""Dashboard exports: cost, performance, and learning metrics.

Generates JSON exports for visualization in:
- Cost dashboards (cost per agent, per project, trends)
- Success rate heatmaps (which agents work best for which projects)
- Cache/performance metrics
- Budget status
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DashboardExporter:
    """Export metrics for monitoring and dashboards."""

    def __init__(self, ml_router, feedback_loop, budget_manager, observability):
        self.ml_router = ml_router
        self.feedback_loop = feedback_loop
        self.budget_manager = budget_manager
        self.observability = observability

    def cost_dashboard(self) -> dict[str, Any]:
        """Export cost metrics."""
        status = self.budget_manager.status()
        ml_summary = self.ml_router.summary()

        # Estimate cost by agent (from ML router data)
        agent_costs = {}
        for project, agents in self.ml_router.stats.items():
            for agent, stats in agents.items():
                if agent not in agent_costs:
                    agent_costs[agent] = {
                        "total_tasks": 0,
                        "successful_tasks": 0,
                        "success_rate": 0.0,
                        "avg_cost": 0.0,
                    }
                agent_costs[agent]["total_tasks"] += stats.total_tasks
                agent_costs[agent]["successful_tasks"] += stats.successful_tasks
                agent_costs[agent]["avg_cost"] = (
                    (agent_costs[agent]["avg_cost"] + stats.avg_cost_usd) / 2
                )

        # Recalculate success rates
        for agent, data in agent_costs.items():
            if data["total_tasks"] > 0:
                data["success_rate"] = round(
                    data["successful_tasks"] / data["total_tasks"], 2
                )

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "budget": {
                "monthly_cap": status.monthly_cap,
                "current_spend": status.current_spend,
                "remaining": status.remaining,
                "percent_used": status.percent_used,
                "is_near_capacity": status.is_near_capacity,
            },
            "agents": agent_costs,
            "projects": ml_summary.get("projects", {}),
        }

    def success_heatmap(self) -> dict[str, Any]:
        """Export success rates per agent per project."""
        heatmap = {}
        for project, agents in self.ml_router.stats.items():
            heatmap[project] = {}
            for agent, stats in agents.items():
                heatmap[project][agent] = {
                    "success_rate": round(stats.success_rate, 2),
                    "tasks": stats.total_tasks,
                    "score": round(stats.score, 2),
                }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "heatmap": heatmap,
        }

    def failure_summary(self) -> dict[str, Any]:
        """Export failure patterns and learning."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self.feedback_loop.summary(),
        }

    def performance_summary(self) -> dict[str, Any]:
        """Export performance metrics (traces, observability)."""
        obs_export = self.observability.export_all()
        metrics = obs_export.get("metrics", {})

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "traces": obs_export.get("traces", []),
            "metrics": {
                "counters": metrics.get("counters", {}),
                "gauges": metrics.get("gauges", {}),
                "histograms": metrics.get("histograms", {}),
            },
        }

    def full_dashboard(self) -> dict[str, Any]:
        """Export all dashboard data."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cost": self.cost_dashboard(),
            "success_rates": self.success_heatmap(),
            "failures": self.failure_summary(),
            "performance": self.performance_summary(),
        }

    def summary_widget(self) -> dict[str, Any]:
        """Minimal summary for UI widget display."""
        status = self.budget_manager.status()
        ml_summary = self.ml_router.summary()

        return {
            "budget_percent_used": status.percent_used,
            "budget_is_near_cap": status.is_near_capacity,
            "projects_with_data": ml_summary.get("projects_with_history", 0),
            "total_tasks_recorded": ml_summary.get("total_tasks_recorded", 0),
            "failure_patterns_detected": len(self.feedback_loop.patterns),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
