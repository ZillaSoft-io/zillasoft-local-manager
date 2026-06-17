"""OpenTelemetry integration for monitoring and dashboards.

Captures traces, metrics, and logs in standard format for export to:
- Jaeger (tracing)
- Prometheus (metrics)
- ELK/Splunk (logs)
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """Distributed trace span."""
    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    attributes: dict[str, Any] = None
    status: str = "ok"

    def __post_init__(self):
        if self.attributes is None:
            self.attributes = {}

    def end(self) -> None:
        """Mark span as complete."""
        self.end_time = time.perf_counter()
        self.duration_ms = (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Export span to dict."""
        return {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
        }


class Tracer:
    """Distributed tracing for observability."""

    def __init__(self, service_name: str = "zillasoft-local-manager"):
        self.service_name = service_name
        self.spans: list[Span] = []

    def start_span(self, name: str, **attributes) -> Span:
        """Start a new span."""
        span = Span(name=name, start_time=time.perf_counter(), attributes=attributes)
        self.spans.append(span)
        logger.debug(f"Span started: {name}")
        return span

    @contextmanager
    def span(self, name: str, **attributes):
        """Context manager for automatic span timing."""
        span = self.start_span(name, **attributes)
        try:
            yield span
        except Exception as e:
            span.status = "error"
            span.attributes["error"] = str(e)
            raise
        finally:
            span.end()
            logger.debug(f"Span ended: {name} ({span.duration_ms:.1f}ms)")

    def export_traces(self) -> list[dict[str, Any]]:
        """Export all spans as trace data."""
        return [s.to_dict() for s in self.spans]

    def clear(self) -> None:
        """Clear recorded spans."""
        self.spans = []


class Metrics:
    """Metrics collection for monitoring."""

    def __init__(self):
        self.counters: dict[str, int] = {}
        self.gauges: dict[str, float] = {}
        self.histograms: dict[str, list[float]] = {}

    def increment_counter(self, name: str, amount: int = 1, **labels) -> None:
        """Increment a counter metric."""
        key = self._key(name, labels)
        self.counters[key] = self.counters.get(key, 0) + amount

    def set_gauge(self, name: str, value: float, **labels) -> None:
        """Set a gauge metric."""
        key = self._key(name, labels)
        self.gauges[key] = value

    def record_histogram(self, name: str, value: float, **labels) -> None:
        """Record a value in a histogram."""
        key = self._key(name, labels)
        if key not in self.histograms:
            self.histograms[key] = []
        self.histograms[key].append(value)

    @staticmethod
    def _key(name: str, labels: dict[str, Any]) -> str:
        """Generate metric key from name and labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def export_metrics(self) -> dict[str, Any]:
        """Export all metrics in Prometheus-compatible format."""
        return {
            "counters": self.counters,
            "gauges": self.gauges,
            "histograms": {
                k: {
                    "count": len(v),
                    "sum": sum(v),
                    "min": min(v),
                    "max": max(v),
                    "p50": sorted(v)[len(v) // 2] if v else 0,
                    "p99": sorted(v)[int(len(v) * 0.99)] if v else 0,
                }
                for k, v in self.histograms.items()
            }
        }


class ObservabilityManager:
    """Central observability: traces + metrics + logs."""

    def __init__(self, service_name: str = "zillasoft-local-manager"):
        self.tracer = Tracer(service_name)
        self.metrics = Metrics()

    def export_all(self) -> dict[str, Any]:
        """Export traces, metrics, and summary."""
        return {
            "service": self.tracer.service_name,
            "traces": self.tracer.export_traces(),
            "metrics": self.metrics.export_metrics(),
        }

    def reset(self) -> None:
        """Reset for next batch of observations."""
        self.tracer.clear()
        self.metrics = Metrics()


# Global singleton
_observability: Optional[ObservabilityManager] = None


def get_observability() -> ObservabilityManager:
    """Get or create global observability manager."""
    global _observability
    if _observability is None:
        _observability = ObservabilityManager()
    return _observability
