"""Concurrent execution of independent agent tasks.

When Sonnet generates multiple independent tasks, execute them in parallel
(e.g., Haiku handles simple task while Opus handles complex task simultaneously).
Reduces wall-clock time and improves responsiveness.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskSpec:
    """Specification for one concurrent task."""
    id: str                          # unique identifier
    agent_label: str                 # "haiku", "opus", etc.
    task_type: str                   # "simple", "complex", "refactor", etc.
    instructions: str                # what to do
    fn: Callable[..., Any]           # async function to execute
    args: tuple = ()
    kwargs: dict = None

    def __post_init__(self):
        if self.kwargs is None:
            self.kwargs = {}


@dataclass
class TaskResult:
    """Result of one task execution."""
    task_id: str
    agent_label: str
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    duration_ms: float = 0.0

    @property
    def is_error(self) -> bool:
        return not self.success


class ConcurrentExecutor:
    """Execute multiple agent tasks in parallel."""

    async def execute_all(self, tasks: list[TaskSpec]) -> list[TaskResult]:
        """Run all tasks concurrently, return results in order.

        Args:
            tasks: list of TaskSpec to execute

        Returns:
            list of TaskResult in same order as input tasks
        """
        if not tasks:
            return []

        logger.info(f"Starting concurrent execution of {len(tasks)} task(s)")
        for task in tasks:
            logger.debug(
                f"  Task {task.id}: {task.agent_label} ({task.task_type})"
            )

        # Run all tasks concurrently using gather
        coroutines = [self._execute_one(task) for task in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=False)

        logger.info(
            f"Concurrent execution complete: {sum(1 for r in results if r.success)} "
            f"succeeded, {sum(1 for r in results if not r.success)} failed"
        )
        return results

    async def _execute_one(self, task: TaskSpec) -> TaskResult:
        """Execute a single task and return result."""
        import time
        start = time.perf_counter()

        try:
            logger.debug(f"Task {task.id}: starting")
            # If fn is async, await it; otherwise run sync in thread pool
            if asyncio.iscoroutinefunction(task.fn):
                result = await task.fn(*task.args, **task.kwargs)
            else:
                # Run sync function in a thread pool to avoid blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: task.fn(*task.args, **task.kwargs)
                )

            duration_ms = (time.perf_counter() - start) * 1000
            logger.debug(
                f"Task {task.id}: success ({duration_ms:.1f}ms)"
            )
            return TaskResult(
                task_id=task.id,
                agent_label=task.agent_label,
                success=True,
                result=result,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                f"Task {task.id}: failed after {duration_ms:.1f}ms: {type(e).__name__}: {e}"
            )
            return TaskResult(
                task_id=task.id,
                agent_label=task.agent_label,
                success=False,
                error=e,
                duration_ms=duration_ms,
            )


def run_tasks_concurrent(tasks: list[TaskSpec]) -> list[TaskResult]:
    """Convenience function: run tasks concurrently and return results.

    Handles event loop creation/cleanup.

    Args:
        tasks: list of TaskSpec to execute

    Returns:
        list of TaskResult in same order as input
    """
    executor = ConcurrentExecutor()
    # Create event loop (handles Windows if needed)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = loop.run_until_complete(executor.execute_all(tasks))
        return results
    finally:
        loop.close()


# Example usage:
#
# async def haiku_task():
#     return haiku.ask("simple rename task...")
#
# async def opus_task():
#     return opus.ask("complex logic task...")
#
# tasks = [
#     TaskSpec(
#         id="task-1",
#         agent_label="haiku",
#         task_type="simple",
#         instructions="Rename variable x to user_id",
#         fn=haiku_task,
#     ),
#     TaskSpec(
#         id="task-2",
#         agent_label="opus",
#         task_type="complex",
#         instructions="Implement new caching layer",
#         fn=opus_task,
#     ),
# ]
#
# results = run_tasks_concurrent(tasks)
# for result in results:
#     print(f"{result.task_id}: {result.agent_label} - "
#           f"{'✓' if result.success else '✗'} ({result.duration_ms:.0f}ms)")
