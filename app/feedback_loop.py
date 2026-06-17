"""Feedback loop optimization: learn from failures, prevent retry traps.

Stores failure patterns and detects when a task is about to repeat a known
failure. Suggests agent swaps or escalation instead of retrying.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class FailurePattern:
    """Recorded failure pattern."""
    error_signature: str       # hash of error message
    error_message: str
    project: str
    agent_tried: str
    task_type: str
    occurrences: int = 1
    last_seen: str = ""
    suggested_agent: Optional[str] = None


class FeedbackLoopManager:
    """Learns from failures, prevents retry traps."""

    def __init__(self, stats_file: str | Path = ".failure_patterns.json"):
        self.stats_file = Path(stats_file)
        self.patterns: dict[str, FailurePattern] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load failure patterns from file."""
        if not self.stats_file.exists():
            return

        try:
            with open(self.stats_file) as f:
                data = json.load(f)
            for sig, pattern_data in data.items():
                self.patterns[sig] = FailurePattern(
                    error_signature=sig,
                    error_message=pattern_data.get("error_message", ""),
                    project=pattern_data.get("project", ""),
                    agent_tried=pattern_data.get("agent_tried", ""),
                    task_type=pattern_data.get("task_type", ""),
                    occurrences=pattern_data.get("occurrences", 1),
                    last_seen=pattern_data.get("last_seen", ""),
                    suggested_agent=pattern_data.get("suggested_agent"),
                )
            logger.info(f"Loaded {len(self.patterns)} failure patterns")
        except Exception as e:
            logger.error(f"Failed to load failure patterns: {e}")

    def _save_patterns(self) -> None:
        """Save failure patterns to file."""
        data = {
            sig: {
                "error_message": p.error_message,
                "project": p.project,
                "agent_tried": p.agent_tried,
                "task_type": p.task_type,
                "occurrences": p.occurrences,
                "last_seen": p.last_seen,
                "suggested_agent": p.suggested_agent,
            }
            for sig, p in self.patterns.items()
        }
        try:
            with open(self.stats_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save failure patterns: {e}")

    @staticmethod
    def _error_signature(error_msg: str) -> str:
        """Generate signature from error message (first 100 chars + hash)."""
        normalized = error_msg[:100].lower().strip()
        h = hashlib.md5(error_msg.encode()).hexdigest()[:8]
        return f"{normalized}#{h}"

    def has_seen_failure(self, error_msg: str, project: str) -> Optional[FailurePattern]:
        """Check if we've seen this error before.

        Returns:
            FailurePattern if seen before, else None
        """
        sig = self._error_signature(error_msg)
        pattern = self.patterns.get(sig)

        if pattern and pattern.project == project:
            return pattern
        return None

    def record_failure(self, error_msg: str, project: str, agent: str,
                      task_type: str, suggested_agent: Optional[str] = None) -> FailurePattern:
        """Record a failure for learning.

        Args:
            error_msg: error message from failed task
            project: project name
            agent: which agent tried and failed
            task_type: type of task (bug_fix, feature, etc.)
            suggested_agent: optional suggestion for next agent to try

        Returns:
            FailurePattern (new or updated)
        """
        from datetime import datetime
        sig = self._error_signature(error_msg)

        if sig in self.patterns:
            pattern = self.patterns[sig]
            pattern.occurrences += 1
            pattern.last_seen = datetime.now().isoformat()
            if suggested_agent:
                pattern.suggested_agent = suggested_agent
        else:
            pattern = FailurePattern(
                error_signature=sig,
                error_message=error_msg,
                project=project,
                agent_tried=agent,
                task_type=task_type,
                last_seen=datetime.now().isoformat(),
                suggested_agent=suggested_agent,
            )
            self.patterns[sig] = pattern

        logger.info(
            f"Recorded failure for {project}/{agent}: {error_msg[:50]}... "
            f"(occurrence #{pattern.occurrences})"
        )
        self._save_patterns()
        return pattern

    def should_escalate(self, error_msg: str, project: str, current_cycle: int,
                       max_cycles: int) -> bool:
        """Determine if we should escalate instead of retry.

        Escalate if:
        - Error was seen before in this project (pattern repeat)
        - We're in final cycle (no retries left)
        - Error seems fundamental (not transient)

        Returns:
            True if escalation is recommended
        """
        pattern = self.has_seen_failure(error_msg, project)
        if not pattern:
            return False

        # Escalate on repeat failures
        if pattern.occurrences >= 2:
            logger.warning(
                f"Escalation recommended: error seen {pattern.occurrences} times before"
            )
            return True

        # Escalate on final cycle
        if current_cycle >= max_cycles:
            return True

        return False

    def suggest_agent_swap(self, error_msg: str, project: str,
                          current_agent: str) -> Optional[str]:
        """Suggest which agent to try next.

        Returns:
            Agent label to try, or None if no suggestion
        """
        pattern = self.has_seen_failure(error_msg, project)
        if not pattern:
            return None

        # If we have a suggestion, return it
        if pattern.suggested_agent and pattern.suggested_agent != current_agent:
            logger.info(
                f"Agent swap suggestion: {current_agent} → {pattern.suggested_agent}"
            )
            return pattern.suggested_agent

        # If error seen before with same agent, swap to opposite
        if pattern.agent_tried == current_agent:
            if current_agent == "haiku":
                logger.info("Swapping to Opus (Haiku failed this before)")
                return "opus"
            elif current_agent == "opus":
                logger.info("Swapping to Haiku (Opus failed this before)")
                return "haiku"

        return None

    def summary(self) -> dict[str, Any]:
        """Summary of learned failures."""
        by_project = {}
        for pattern in self.patterns.values():
            if pattern.project not in by_project:
                by_project[pattern.project] = []
            by_project[pattern.project].append({
                "error": pattern.error_message[:50],
                "agent": pattern.agent_tried,
                "occurrences": pattern.occurrences,
                "suggested_swap": pattern.suggested_agent,
            })

        return {
            "total_failure_patterns": len(self.patterns),
            "by_project": by_project,
        }


# Global singleton
_feedback_loop: Optional[FeedbackLoopManager] = None


def get_feedback_loop() -> FeedbackLoopManager:
    """Get or create global feedback loop manager."""
    global _feedback_loop
    if _feedback_loop is None:
        _feedback_loop = FeedbackLoopManager()
    return _feedback_loop
