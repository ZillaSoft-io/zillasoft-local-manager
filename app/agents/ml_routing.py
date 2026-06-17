"""ML-based intelligent routing: learn which agent works best.

Tracks success rates per agent per project, routes new tasks to the
agent with highest historical success rate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentStats:
    """Success statistics for one agent."""
    agent_label: str
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_cost_usd: float = 0.0
    avg_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        """Success rate 0.0-1.0."""
        if self.total_tasks == 0:
            return 0.5  # Default to neutral
        return self.successful_tasks / self.total_tasks

    @property
    def score(self) -> float:
        """Composite score: success rate * (1 - cost factor).

        Higher is better.
        """
        cost_factor = min(self.avg_cost_usd / 10.0, 1.0)  # Normalize to 0-1
        return self.success_rate * (1.0 - cost_factor * 0.3)  # Cost 30% weight


class MLRouter:
    """Machine learning-based routing."""

    def __init__(self, stats_file: str | Path = ".ml_stats.json"):
        self.stats_file = Path(stats_file)
        self.stats: dict[str, dict[str, AgentStats]] = {}  # project -> agent -> stats
        self._load_stats()

    def _load_stats(self) -> None:
        """Load stats from file."""
        if not self.stats_file.exists():
            logger.debug(f"No stats file at {self.stats_file}, starting fresh")
            return

        try:
            with open(self.stats_file) as f:
                data = json.load(f)
            for project, agents in data.items():
                self.stats[project] = {
                    agent: AgentStats(
                        agent_label=agent,
                        total_tasks=stats["total_tasks"],
                        successful_tasks=stats["successful_tasks"],
                        failed_tasks=stats["failed_tasks"],
                        avg_cost_usd=stats.get("avg_cost_usd", 0.0),
                        avg_duration_ms=stats.get("avg_duration_ms", 0.0),
                    )
                    for agent, stats in agents.items()
                }
            logger.info(f"Loaded ML stats for {len(self.stats)} projects")
        except Exception as e:
            logger.error(f"Failed to load stats: {e}")

    def _save_stats(self) -> None:
        """Save stats to file."""
        data = {
            project: {
                agent: {
                    "agent_label": stats.agent_label,
                    "total_tasks": stats.total_tasks,
                    "successful_tasks": stats.successful_tasks,
                    "failed_tasks": stats.failed_tasks,
                    "avg_cost_usd": stats.avg_cost_usd,
                    "avg_duration_ms": stats.avg_duration_ms,
                }
                for agent, stats in agents.items()
            }
            for project, agents in self.stats.items()
        }
        try:
            with open(self.stats_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save stats: {e}")

    def best_agent(self, project: str) -> str:
        """Get best-performing agent for project.

        Falls back to keyword-based routing if no history.

        Returns:
            Agent label ("haiku", "opus", etc.)
        """
        if project not in self.stats or not self.stats[project]:
            # No history: default to routing (let caller decide)
            return "default"

        agents = self.stats[project]
        best = max(agents.values(), key=lambda a: a.score)
        logger.debug(
            f"ML routing for {project}: {best.agent_label} "
            f"(success_rate={best.success_rate:.0%}, score={best.score:.2f})"
        )
        return best.agent_label

    def record_task(self, project: str, agent: str, success: bool,
                   cost_usd: float = 0.0, duration_ms: float = 0.0) -> None:
        """Record task outcome to learn from.

        Args:
            project: project name
            agent: agent label used
            success: whether task succeeded
            cost_usd: cost of the task
            duration_ms: execution duration
        """
        if project not in self.stats:
            self.stats[project] = {}

        if agent not in self.stats[project]:
            self.stats[project][agent] = AgentStats(agent_label=agent)

        stats = self.stats[project][agent]
        stats.total_tasks += 1

        if success:
            stats.successful_tasks += 1
        else:
            stats.failed_tasks += 1

        # Update running averages
        if stats.avg_cost_usd == 0:
            stats.avg_cost_usd = cost_usd
        else:
            stats.avg_cost_usd = (stats.avg_cost_usd + cost_usd) / 2

        if stats.avg_duration_ms == 0:
            stats.avg_duration_ms = duration_ms
        else:
            stats.avg_duration_ms = (stats.avg_duration_ms + duration_ms) / 2

        logger.debug(
            f"Recorded {project}/{agent}: success={success}, "
            f"cost=${cost_usd:.2f}, duration={duration_ms:.0f}ms"
        )
        self._save_stats()

    def get_stats(self, project: str) -> dict[str, AgentStats]:
        """Get all agent stats for a project."""
        return self.stats.get(project, {})

    def summary(self) -> dict[str, Any]:
        """Summary across all projects."""
        total_projects = len(self.stats)
        total_tasks = sum(
            stats.total_tasks
            for project_agents in self.stats.values()
            for stats in project_agents.values()
        )
        return {
            "projects_with_history": total_projects,
            "total_tasks_recorded": total_tasks,
            "projects": {
                project: {
                    "best_agent": self.best_agent(project),
                    "agents": {
                        agent: {
                            "success_rate": f"{stats.success_rate:.0%}",
                            "tasks": stats.total_tasks,
                            "score": f"{stats.score:.2f}",
                        }
                        for agent, stats in agents.items()
                    }
                }
                for project, agents in self.stats.items()
            }
        }


# Global singleton
_ml_router: Optional[MLRouter] = None


def get_ml_router() -> MLRouter:
    """Get or create global ML router."""
    global _ml_router
    if _ml_router is None:
        _ml_router = MLRouter()
    return _ml_router
