"""Circuit breaker for external API calls (GitHub, Railway, Jira, etc.).

Prevents cascading failures: if an API fails 3 times in a row, stop retrying
for 60 seconds. Alerts the user instead of burning tokens on doomed retries.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """States of a circuit breaker."""
    CLOSED = "closed"           # Normal operation
    OPEN = "open"               # Too many failures, reject calls
    HALF_OPEN = "half_open"     # Testing if service recovered


class CircuitBreaker:
    """Circuit breaker for external API endpoints.

    Prevents retry storms:
    - CLOSED: accept calls (normal)
    - OPEN: reject calls fast (after N failures)
    - HALF_OPEN: allow one test call (after timeout, see if service recovered)
    """

    def __init__(self, name: str, failure_threshold: int = 3,
                 recovery_timeout_secs: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_secs = recovery_timeout_secs
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_failure_time: Optional[datetime] = None
        self.last_open_time: Optional[datetime] = None

    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed."""
        if self.state != CircuitState.OPEN or not self.last_open_time:
            return False
        elapsed = datetime.now(timezone.utc) - self.last_open_time
        return elapsed >= timedelta(seconds=self.recovery_timeout_secs)

    def _attempt_reset(self) -> None:
        """Transition from OPEN to HALF_OPEN to test recovery."""
        self.state = CircuitState.HALF_OPEN
        self.failure_count = 0
        logger.info(f"Circuit {self.name}: HALF_OPEN (testing recovery)")

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Execute fn() with circuit breaker protection.

        Args:
            fn: callable to execute
            *args, **kwargs: passed to fn()

        Returns:
            Result of fn()

        Raises:
            CircuitBreakerOpen: if circuit is open
            Exception: any exception from fn()
        """
        # Check if we should attempt recovery
        if self._should_attempt_reset():
            self._attempt_reset()

        # Reject calls if circuit is open
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpen(
                f"Circuit {self.name} is OPEN. Retry after "
                f"{self.recovery_timeout_secs}s"
            )

        # Attempt the call
        try:
            result = fn(*args, **kwargs)
            # Success: reset to closed
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info(f"Circuit {self.name}: CLOSED (service recovered)")
            return result
        except Exception as e:
            # Failure: increment counter and potentially open
            self.failure_count += 1
            self.last_failure_time = datetime.now(timezone.utc)
            logger.warning(
                f"Circuit {self.name}: failure {self.failure_count}/"
                f"{self.failure_threshold} ({type(e).__name__})"
            )
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.last_open_time = datetime.now(timezone.utc)
                logger.error(
                    f"Circuit {self.name}: OPEN (failed {self.failure_count} times, "
                    f"will recover in {self.recovery_timeout_secs}s)"
                )
            raise

    @property
    def status(self) -> dict[str, Any]:
        """Return circuit status for logging/monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time.isoformat()
                if self.last_failure_time else None,
        }


class CircuitBreakerOpen(Exception):
    """Raised when a circuit is open and a call is attempted."""
    pass


# Convenience: create a registry of breakers for common endpoints
class CircuitBreakerRegistry:
    """Global registry of circuit breakers for different services."""

    def __init__(self):
        self.breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(self, name: str, **kwargs) -> CircuitBreaker:
        """Get or create a breaker by name."""
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(name, **kwargs)
        return self.breakers[name]

    def reset_all(self) -> None:
        """Reset all breakers to CLOSED."""
        for breaker in self.breakers.values():
            breaker.state = CircuitState.CLOSED
            breaker.failure_count = 0

    @property
    def status(self) -> dict[str, Any]:
        """Status of all breakers."""
        return {name: cb.status for name, cb in self.breakers.items()}


_registry = CircuitBreakerRegistry()


def get_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker by name."""
    return _registry.get_or_create(name)
