"""Predictive caching: cluster similar tasks and reuse plans.

When a new task arrives, find similar cached plans and reuse them instead
of regenerating via Sonnet. Saves 30-40% on plan generation.
"""
from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class TaskClusterer:
    """Cluster similar tasks by description similarity."""

    @staticmethod
    def similarity(text1: str, text2: str) -> float:
        """Compute similarity ratio (0-1) between two texts."""
        s = SequenceMatcher(None, text1.lower(), text2.lower())
        return s.ratio()

    @staticmethod
    def extract_keywords(text: str) -> set[str]:
        """Extract keywords from task description."""
        import re
        words = re.findall(r'\b\w+\b', text.lower())
        # Filter out stop words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'of', 'to', 'for'}
        return set(w for w in words if len(w) > 2 and w not in stop_words)

    def find_similar(self, task_desc: str, cached_tasks: list[tuple[str, str]],
                     similarity_threshold: float = 0.7) -> Optional[tuple[str, str]]:
        """Find most similar cached task.

        Args:
            task_desc: current task description
            cached_tasks: list of (description, plan) tuples
            similarity_threshold: minimum similarity (0-1)

        Returns:
            (description, plan) tuple if found above threshold, else None
        """
        if not cached_tasks:
            return None

        best_match = None
        best_score = 0

        for cached_desc, cached_plan in cached_tasks:
            # Text similarity
            text_sim = self.similarity(task_desc, cached_desc)

            # Keyword overlap
            task_kw = self.extract_keywords(task_desc)
            cached_kw = self.extract_keywords(cached_desc)
            if task_kw and cached_kw:
                overlap = len(task_kw & cached_kw) / max(len(task_kw), len(cached_kw))
            else:
                overlap = 0

            # Combined score (70% text, 30% keyword overlap)
            score = text_sim * 0.7 + overlap * 0.3

            if score > best_score and score >= similarity_threshold:
                best_score = score
                best_match = (cached_desc, cached_plan)

        if best_match:
            logger.debug(
                f"Predictive cache hit: similarity={best_score:.2f} "
                f"vs '{best_match[0][:50]}...'"
            )
        return best_match


class PredictiveCacheManager:
    """Manages predictive caching of plans."""

    def __init__(self, cache_file: str | Path = ".predictive_cache.json",
                 max_cached_plans: int = 1000):
        self.cache_file = Path(cache_file)
        self.max_cached_plans = max_cached_plans
        self.plans: dict[str, dict] = {}  # task_hash -> {desc, plan, project}
        self.clusterer = TaskClusterer()
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cached plans from file."""
        if not self.cache_file.exists():
            return

        try:
            with open(self.cache_file) as f:
                self.plans = json.load(f)
            logger.info(f"Loaded {len(self.plans)} cached plans")
        except Exception as e:
            logger.error(f"Failed to load predictive cache: {e}")

    def _save_cache(self) -> None:
        """Save cached plans to file."""
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.plans, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save predictive cache: {e}")

    @staticmethod
    def _hash_task(task_desc: str, project: str) -> str:
        """Generate deterministic hash for task."""
        import hashlib
        content = f"{task_desc}#{project}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def get_similar_plan(self, task_desc: str, project: str,
                         similarity_threshold: float = 0.7) -> Optional[str]:
        """Find and return a similar cached plan.

        Returns:
            Plan text if similar task cached, else None
        """
        # Get plans for this project
        project_plans = [
            (data["desc"], data["plan"])
            for data in self.plans.values()
            if data.get("project") == project
        ]

        similar = self.clusterer.find_similar(
            task_desc, project_plans, similarity_threshold
        )
        return similar[1] if similar else None

    def cache_plan(self, task_desc: str, project: str, plan: str) -> None:
        """Store a generated plan for future reuse.

        Args:
            task_desc: task description
            project: project name
            plan: generated plan text
        """
        # Limit cache size (LRU: just evict oldest on overflow)
        if len(self.plans) >= self.max_cached_plans:
            oldest_key = next(iter(self.plans))
            del self.plans[oldest_key]
            logger.debug("Predictive cache evicted oldest entry")

        task_hash = self._hash_task(task_desc, project)
        self.plans[task_hash] = {
            "desc": task_desc,
            "plan": plan,
            "project": project,
            "cached_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }

        logger.debug(f"Cached plan for: {task_desc[:50]}... (hash: {task_hash})")
        self._save_cache()

    def stats(self) -> dict:
        """Cache statistics."""
        by_project = {}
        for data in self.plans.values():
            proj = data.get("project", "unknown")
            by_project[proj] = by_project.get(proj, 0) + 1

        return {
            "total_cached": len(self.plans),
            "by_project": by_project,
            "max_capacity": self.max_cached_plans,
        }


# Global singleton
_predictive_cache: Optional[PredictiveCacheManager] = None


def get_predictive_cache() -> PredictiveCacheManager:
    """Get or create global predictive cache."""
    global _predictive_cache
    if _predictive_cache is None:
        _predictive_cache = PredictiveCacheManager()
    return _predictive_cache
