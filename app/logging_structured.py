"""Structured JSON logging for observability and debugging.

Emits JSON logs with context (session_id, cycle, agent, step) so failures
can be traced and patterns detected (e.g., "Sonnet fails on React 60% of the time").
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

# Standard structured log fields
LOG_SESSION = "session_id"
LOG_CYCLE = "cycle"
LOG_AGENT = "agent"
LOG_STEP = "step"
LOG_TOKENS = "tokens"
LOG_COST = "cost_usd"
LOG_ERROR = "error"
LOG_DURATION_MS = "duration_ms"


class StructuredLogger:
    """Wrapper around Python logging for structured JSON output."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def _emit(self, level: int, message: str, **context) -> None:
        """Emit a structured log entry."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "level": logging.getLevelName(level),
            **context,
        }
        json_str = json.dumps(record, default=str)
        self.logger.log(level, json_str)

    # Convenience methods
    def info(self, message: str, **context) -> None:
        self._emit(logging.INFO, message, **context)

    def warning(self, message: str, **context) -> None:
        self._emit(logging.WARNING, message, **context)

    def error(self, message: str, **context) -> None:
        self._emit(logging.ERROR, message, **context)

    def debug(self, message: str, **context) -> None:
        self._emit(logging.DEBUG, message, **context)

    def critical(self, message: str, **context) -> None:
        self._emit(logging.CRITICAL, message, **context)


def setup_structured_logging(log_level: str = "INFO") -> None:
    """Configure Python logging to emit structured JSON to stdout/stderr.

    Call once at app startup.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))


# Example usage in agent code:
# logger = StructuredLogger(__name__)
# logger.info("Haiku: plan validation", session_id=session_id, approved=False,
#             corrections="...")
