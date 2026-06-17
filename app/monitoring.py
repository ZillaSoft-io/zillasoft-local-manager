"""Prometheus metrics and alerting for monitoring.

Exports metrics in Prometheus format. Integrates with Grafana for dashboards.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PrometheusMetrics:
    """Prometheus-format metrics."""
    # Counters
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_escalated: int = 0

    # Gauges
    budget_spent_usd: float = 0.0
    budget_remaining_usd: float = 0.0
    cache_hit_rate: float = 0.0
    ml_router_patterns: int = 0

    # Histograms (buckets)
    task_duration_seconds: list[float] = None
    task_cost_usd: list[float] = None

    def __post_init__(self):
        if self.task_duration_seconds is None:
            self.task_duration_seconds = []
        if self.task_cost_usd is None:
            self.task_cost_usd = []

    def to_prometheus_text(self) -> str:
        """Export in Prometheus text format."""
        lines = []
        lines.append("# HELP zillasoft_tasks_started Total tasks started")
        lines.append("# TYPE zillasoft_tasks_started counter")
        lines.append(f"zillasoft_tasks_started {self.tasks_started}")

        lines.append("# HELP zillasoft_tasks_completed Total tasks completed")
        lines.append("# TYPE zillasoft_tasks_completed counter")
        lines.append(f"zillasoft_tasks_completed {self.tasks_completed}")

        lines.append("# HELP zillasoft_tasks_failed Total tasks failed")
        lines.append("# TYPE zillasoft_tasks_failed counter")
        lines.append(f"zillasoft_tasks_failed {self.tasks_failed}")

        lines.append("# HELP zillasoft_tasks_escalated Total tasks escalated")
        lines.append("# TYPE zillasoft_tasks_escalated counter")
        lines.append(f"zillasoft_tasks_escalated {self.tasks_escalated}")

        lines.append("# HELP zillasoft_budget_spent_usd USD spent this month")
        lines.append("# TYPE zillasoft_budget_spent_usd gauge")
        lines.append(f"zillasoft_budget_spent_usd {self.budget_spent_usd}")

        lines.append("# HELP zillasoft_budget_remaining_usd USD remaining")
        lines.append("# TYPE zillasoft_budget_remaining_usd gauge")
        lines.append(f"zillasoft_budget_remaining_usd {self.budget_remaining_usd}")

        lines.append("# HELP zillasoft_cache_hit_rate Cache hit rate (0-1)")
        lines.append("# TYPE zillasoft_cache_hit_rate gauge")
        lines.append(f"zillasoft_cache_hit_rate {self.cache_hit_rate}")

        lines.append("# HELP zillasoft_ml_patterns ML failure patterns detected")
        lines.append("# TYPE zillasoft_ml_patterns gauge")
        lines.append(f"zillasoft_ml_patterns {self.ml_router_patterns}")

        # Histogram: task duration
        if self.task_duration_seconds:
            lines.append("# HELP zillasoft_task_duration_seconds Task execution time")
            lines.append("# TYPE zillasoft_task_duration_seconds histogram")
            for i, value in enumerate(sorted(self.task_duration_seconds)):
                lines.append(f"zillasoft_task_duration_seconds_bucket{{le=\"{value}\"}} {i + 1}")
            lines.append(f"zillasoft_task_duration_seconds_bucket{{le=\"+Inf\"}} {len(self.task_duration_seconds)}")
            lines.append(f"zillasoft_task_duration_seconds_sum {sum(self.task_duration_seconds)}")
            lines.append(f"zillasoft_task_duration_seconds_count {len(self.task_duration_seconds)}")

        # Histogram: task cost
        if self.task_cost_usd:
            lines.append("# HELP zillasoft_task_cost_usd Task cost in USD")
            lines.append("# TYPE zillasoft_task_cost_usd histogram")
            for i, value in enumerate(sorted(self.task_cost_usd)):
                lines.append(f"zillasoft_task_cost_usd_bucket{{le=\"{value}\"}} {i + 1}")
            lines.append(f"zillasoft_task_cost_usd_bucket{{le=\"+Inf\"}} {len(self.task_cost_usd)}")
            lines.append(f"zillasoft_task_cost_usd_sum {sum(self.task_cost_usd)}")
            lines.append(f"zillasoft_task_cost_usd_count {len(self.task_cost_usd)}")

        return "\n".join(lines)


class AlertingEngine:
    """Alert generation for anomalies."""

    def __init__(self, budget_cap: float = 100.0):
        self.budget_cap = budget_cap
        self.alerts: list[dict] = []

    def check_budget_threshold(self, spent: float, threshold_pct: float = 80.0) -> Optional[str]:
        """Alert if budget exceeds threshold."""
        percent = (spent / self.budget_cap) * 100
        if percent >= threshold_pct:
            return f"Budget {percent:.1f}% spent (${spent:.2f} / ${self.budget_cap:.2f})"
        return None

    def check_failure_rate(self, failed: int, total: int, threshold_pct: float = 30.0) -> Optional[str]:
        """Alert if failure rate exceeds threshold."""
        if total == 0:
            return None
        rate = (failed / total) * 100
        if rate >= threshold_pct:
            return f"High failure rate: {rate:.1f}% ({failed}/{total})"
        return None

    def check_cache_thrashing(self, hit_rate: float, threshold: float = 0.2) -> Optional[str]:
        """Alert if cache hit rate is too low."""
        if hit_rate < threshold:
            return f"Low cache hit rate: {hit_rate:.1%} (threshold: {threshold:.1%})"
        return None

    def check_repeated_failures(self, pattern_count: int, threshold: int = 5) -> Optional[str]:
        """Alert if too many failure patterns detected."""
        if pattern_count >= threshold:
            return f"Too many failure patterns: {pattern_count} (threshold: {threshold})"
        return None

    def generate_alerts(self, metrics: PrometheusMetrics, spent: float) -> list[str]:
        """Generate all applicable alerts."""
        alerts = []

        budget_alert = self.check_budget_threshold(spent)
        if budget_alert:
            alerts.append(budget_alert)

        failure_alert = self.check_failure_rate(
            metrics.tasks_failed,
            metrics.tasks_completed,
        )
        if failure_alert:
            alerts.append(failure_alert)

        cache_alert = self.check_cache_thrashing(metrics.cache_hit_rate)
        if cache_alert:
            alerts.append(cache_alert)

        pattern_alert = self.check_repeated_failures(metrics.ml_router_patterns)
        if pattern_alert:
            alerts.append(pattern_alert)

        return alerts
