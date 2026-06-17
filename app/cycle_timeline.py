"""UI 4: Cycle timeline tracking — show timing breakdown per cycle.

Records what agent ran, how long each step took, for transparency.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CycleStep:
    """A single step in a cycle."""
    name: str  # "implementation", "test_run", "test_review", etc
    agent: str  # "opus", "haiku", "sonnet"
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_ms: float = 0.0
    status: str = "in_progress"  # in_progress, completed, failed

    def complete(self) -> None:
        """Mark step as complete."""
        self.end_time = datetime.now(timezone.utc)
        self.duration_ms = (self.end_time - self.start_time).total_seconds() * 1000
        self.status = "completed"

    def fail(self) -> None:
        """Mark step as failed."""
        self.end_time = datetime.now(timezone.utc)
        self.duration_ms = (self.end_time - self.start_time).total_seconds() * 1000
        self.status = "failed"

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "name": self.name,
            "agent": self.agent,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
        }


@dataclass
class CycleTimeline:
    """Timeline of all steps in a cycle."""
    cycle_num: int
    steps: list[CycleStep] = field(default_factory=list)
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    total_duration_ms: float = 0.0
    status: str = "in_progress"

    def add_step(self, name: str, agent: str) -> CycleStep:
        """Start a new step."""
        step = CycleStep(
            name=name,
            agent=agent,
            start_time=datetime.now(timezone.utc)
        )
        self.steps.append(step)
        return step

    def complete(self) -> None:
        """Mark cycle as complete."""
        self.end_time = datetime.now(timezone.utc)
        self.total_duration_ms = (self.end_time - self.start_time).total_seconds() * 1000
        self.status = "completed"

    def format_summary(self) -> str:
        """Format as human-readable summary.

        Example: "Cycle 1: [opus impl 2.3s] [haiku test 0.5s] [sonnet review 1.8s] = 4.6s total"
        """
        step_summaries = [
            f"[{s.agent[:3]} {s.name[:6]} {s.duration_ms/1000:.1f}s]"
            for s in self.steps
        ]
        return f"Cycle {self.cycle_num}: {' '.join(step_summaries)} = {self.total_duration_ms/1000:.1f}s total"

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            "cycle_num": self.cycle_num,
            "steps": [s.to_dict() for s in self.steps],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "status": self.status,
            "summary": self.format_summary(),
        }


class SessionTimelines:
    """Tracks timelines for all cycles in a session."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.timelines: dict[int, CycleTimeline] = {}
        self.current_cycle: Optional[CycleTimeline] = None

    def start_cycle(self, cycle_num: int) -> CycleTimeline:
        """Start a new cycle."""
        timeline = CycleTimeline(cycle_num=cycle_num)
        self.timelines[cycle_num] = timeline
        self.current_cycle = timeline
        logger.debug(f"Started cycle {cycle_num} timeline")
        return timeline

    def get_summary(self) -> dict:
        """Get summary of all cycles."""
        completed_cycles = [
            t.to_dict()
            for t in sorted(self.timelines.values(), key=lambda x: x.cycle_num)
            if t.status == "completed"
        ]
        return {
            "session_id": self.session_id,
            "completed_cycles": len(completed_cycles),
            "timelines": completed_cycles,
            "total_session_time_ms": sum(t.total_duration_ms for t in self.timelines.values()),
        }


# Global timeline tracker per session
_timelines: dict[str, SessionTimelines] = {}


def get_session_timelines(session_id: str) -> SessionTimelines:
    """Get or create timeline tracker for a session."""
    if session_id not in _timelines:
        _timelines[session_id] = SessionTimelines(session_id)
    return _timelines[session_id]


def cleanup_session_timeline(session_id: str) -> None:
    """Clean up timeline tracker after session completes."""
    if session_id in _timelines:
        del _timelines[session_id]
