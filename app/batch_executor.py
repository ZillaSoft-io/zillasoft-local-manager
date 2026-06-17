"""Batch parallel session executor: run multiple independent sessions concurrently.

Executes multiple sessions in parallel via asyncio, saving 3-4x wall-clock time
for bulk work.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class BatchTask:
    """A task in a batch execution."""
    session_id: str
    project: str
    task_type: str
    description: str
    run_fn: Callable  # async function to execute


@dataclass
class BatchResult:
    """Result of a batch execution."""
    session_id: str
    status: str  # "success", "failed", "pending"
    result: Optional[dict] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


class BatchExecutor:
    """Execute multiple sessions in parallel."""

    def __init__(self, max_parallel: int = 4):
        """Initialize batch executor.

        Args:
            max_parallel: max sessions to run simultaneously
        """
        self.max_parallel = max_parallel
        self.results: dict[str, BatchResult] = {}
        logger.info(f"BatchExecutor initialized with {max_parallel} parallel slots")

    async def execute_batch(self, tasks: list[BatchTask]) -> dict[str, BatchResult]:
        """Execute multiple tasks in parallel.

        Args:
            tasks: list of BatchTask to execute

        Returns:
            dict of session_id -> BatchResult
        """
        logger.info(f"Starting batch execution of {len(tasks)} tasks")

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_with_semaphore(task: BatchTask) -> BatchResult:
            async with semaphore:
                return await self._execute_single(task)

        # Run all tasks concurrently
        results = await asyncio.gather(
            *[run_with_semaphore(task) for task in tasks],
            return_exceptions=False
        )

        # Map results by session_id
        self.results = {r.session_id: r for r in results}

        # Log summary
        successful = sum(1 for r in results if r.status == "success")
        failed = sum(1 for r in results if r.status == "failed")
        logger.info(
            f"Batch complete: {successful} succeeded, {failed} failed "
            f"({len(tasks)} total)"
        )

        return self.results

    async def _execute_single(self, task: BatchTask) -> BatchResult:
        """Execute a single task with timing and error handling."""
        import time

        start = time.time()
        logger.info(f"Starting task: {task.session_id} ({task.project})")

        try:
            result = await task.run_fn()
            duration_ms = (time.time() - start) * 1000

            return BatchResult(
                session_id=task.session_id,
                status="success",
                result=result,
                duration_ms=duration_ms
            )

        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            error_str = f"{type(e).__name__}: {str(e)}"

            logger.error(
                f"Task failed: {task.session_id} after {duration_ms:.0f}ms: {error_str}"
            )

            return BatchResult(
                session_id=task.session_id,
                status="failed",
                error=error_str,
                duration_ms=duration_ms
            )

    def get_summary(self) -> dict:
        """Get summary of batch execution results."""
        if not self.results:
            return {"status": "no_results"}

        successful = [r for r in self.results.values() if r.status == "success"]
        failed = [r for r in self.results.values() if r.status == "failed"]
        total_time = sum(r.duration_ms for r in self.results.values())

        return {
            "total_tasks": len(self.results),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": round(len(successful) / len(self.results) * 100, 1),
            "total_duration_ms": round(total_time, 0),
            "avg_duration_ms": round(total_time / len(self.results), 0),
            "speedup_vs_serial": round(total_time / max(r.duration_ms for r in self.results.values()), 1),
        }


# Global batch executor
_executor: BatchExecutor | None = None


def get_batch_executor(max_parallel: int = 4) -> BatchExecutor:
    """Get or create global batch executor."""
    global _executor
    if _executor is None:
        _executor = BatchExecutor(max_parallel)
    return _executor


async def run_batch_sessions(
    orchestrator,
    session_ids: list[str]
) -> dict[str, BatchResult]:
    """Run multiple sessions in parallel.

    Args:
        orchestrator: Orchestrator instance
        session_ids: list of session IDs to execute

    Returns:
        dict of session_id -> BatchResult
    """
    batch = get_batch_executor(max_parallel=4)

    tasks = [
        BatchTask(
            session_id=sid,
            project=orchestrator._db.get_session(sid).get("project", "unknown"),
            task_type=orchestrator._db.get_session(sid).get("task_type", "unknown"),
            description=f"Session {sid[:8]}",
            run_fn=lambda s=sid: asyncio.to_thread(orchestrator.run_session, s)
        )
        for sid in session_ids
    ]

    return await batch.execute_batch(tasks)
