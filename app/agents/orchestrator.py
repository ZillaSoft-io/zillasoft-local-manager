"""Agent orchestration: routing, caching, cost tracking, and parallel execution.

Ties together:
1. Plan generation (Sonnet)
2. Plan analysis and routing (Haiku vs Opus decision)
3. Parallel task execution
4. Cost tracking and structured logging
5. Session caching
"""
from __future__ import annotations

import logging
from typing import Optional

from ..cache import SessionCache, cache_plan, get_cached_plan
from ..cost.breakdown import AgentCost, CycleBreakdown, build_cycle_breakdown
from ..logging_structured import StructuredLogger
from ..resilience import get_breaker
from .base import Agent
from .concurrent import ConcurrentExecutor, TaskResult, TaskSpec
from .routing import analyze_plan, should_use_cheap_agent
from .usage import UsageTracker

logger = logging.getLogger(__name__)
struct_logger = StructuredLogger(__name__)


class AgentOrchestrator:
    """Orchestrates Sonnet → routing → parallel execution → cost tracking."""

    def __init__(self, haiku: Agent, sonnet: Agent, opus: Agent,
                 cache: SessionCache, session_id: str):
        self.haiku = haiku
        self.sonnet = sonnet
        self.opus = opus
        self.cache = cache
        self.session_id = session_id
        self.usage_tracker = UsageTracker()
        self.cycle_num = 0
        self.circuit_breaker = get_breaker("sonnet_plan_generation")

    def plan_phase(self, context: str, effort: Optional[str] = None) -> Optional[str]:
        """Phase 1: Generate and cache plan via Sonnet.

        `effort` scales the planning reasoning depth (None = model default).

        Returns:
            Plan text, or None if failed
        """
        self.cycle_num += 1

        # Cache key: hash of the FULL context. Keying on context[:100] (the old
        # behaviour) collided on contexts sharing a prefix and missed on tiny
        # edits past char 100.
        import hashlib
        cache_key = hashlib.sha256(context.encode("utf-8")).hexdigest()

        # Check cache first
        cached_plan = get_cached_plan(self.cache, cache_key)
        if cached_plan:
            struct_logger.info(
                "Plan generation (cached)",
                session_id=self.session_id,
                cycle=self.cycle_num,
                agent="sonnet",
                source="cache",
            )
            return cached_plan

        # Generate plan with circuit breaker protection
        try:
            response = self.circuit_breaker.call(
                self.sonnet.generate_dry_run_plan,
                context,
                effort,
            )
            # Track usage
            self.usage_tracker.record("sonnet", self.sonnet.model, response.usage)

            # Cache for future use
            cache_plan(self.cache, cache_key, response.text)

            struct_logger.info(
                "Plan generation (generated)",
                session_id=self.session_id,
                cycle=self.cycle_num,
                agent="sonnet",
                tokens=response.usage.total_input,
                source="generation",
            )
            return response.text
        except Exception as e:
            struct_logger.error(
                "Plan generation failed",
                session_id=self.session_id,
                cycle=self.cycle_num,
                agent="sonnet",
                error=str(e),
            )
            return None

    def routing_phase(self, plan: str) -> str:
        """Phase 2: Analyze plan and decide which agent to use.

        Returns:
            Agent label ("haiku" or "opus")
        """
        decision = analyze_plan(plan)
        agent = decision.value
        struct_logger.info(
            "Plan routing decision",
            session_id=self.session_id,
            cycle=self.cycle_num,
            decision=agent,
            reason="simple" if agent == "haiku" else "complex",
        )
        return agent

    def execution_phase(self, agent_label: str, instructions: str) -> Optional[str]:
        """Phase 3: Execute instructions with selected agent.

        Returns:
            Implementation result, or None if failed
        """
        agent = self.haiku if agent_label == "haiku" else self.opus

        try:
            response = agent.ask(instructions)
            self.usage_tracker.record(agent_label, agent.model, response.usage)

            struct_logger.info(
                "Implementation complete",
                session_id=self.session_id,
                cycle=self.cycle_num,
                agent=agent_label,
                tokens=response.usage.total_input,
            )
            return response.text
        except Exception as e:
            struct_logger.error(
                "Implementation failed",
                session_id=self.session_id,
                cycle=self.cycle_num,
                agent=agent_label,
                error=str(e),
            )
            return None

    async def execution_phase_async(
        self, agent_label: str, instructions: str
    ) -> Optional[str]:
        """Async version of execution_phase for concurrent execution."""
        return self.execution_phase(agent_label, instructions)

    def parallel_execution_phase(
        self, simple_instructions: str, complex_instructions: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Phase 3 (parallel): Execute both Haiku and Opus tasks concurrently.

        Args:
            simple_instructions: what Haiku should do
            complex_instructions: what Opus should do

        Returns:
            (haiku_result, opus_result) tuple
        """
        import asyncio

        async def run_parallel():
            executor = ConcurrentExecutor()
            tasks = [
                TaskSpec(
                    id="haiku-task",
                    agent_label="haiku",
                    task_type="simple",
                    instructions=simple_instructions,
                    fn=self.execution_phase_async,
                    kwargs={"agent_label": "haiku", "instructions": simple_instructions},
                ),
                TaskSpec(
                    id="opus-task",
                    agent_label="opus",
                    task_type="complex",
                    instructions=complex_instructions,
                    fn=self.execution_phase_async,
                    kwargs={"agent_label": "opus", "instructions": complex_instructions},
                ),
            ]
            results = await executor.execute_all(tasks)
            return results

        # Run async tasks
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        results = loop.run_until_complete(run_parallel())

        # Extract results in order
        haiku_result = None
        opus_result = None
        for result in results:
            if result.task_id == "haiku-task" and result.success:
                haiku_result = result.result
            elif result.task_id == "opus-task" and result.success:
                opus_result = result.result

        struct_logger.info(
            "Parallel execution complete",
            session_id=self.session_id,
            cycle=self.cycle_num,
            haiku_success=any(r.task_id == "haiku-task" and r.success for r in results),
            opus_success=any(r.task_id == "opus-task" and r.success for r in results),
        )
        return (haiku_result, opus_result)

    def cost_phase(self) -> CycleBreakdown:
        """Phase 4: Generate cost breakdown for this cycle.

        Returns:
            CycleBreakdown with per-agent costs
        """
        agent_models = {
            "haiku": self.haiku.model,
            "sonnet": self.sonnet.model,
            "opus": self.opus.model,
        }
        breakdown = build_cycle_breakdown(
            cycle_num=self.cycle_num,
            tracker=self.usage_tracker,
            agent_models=agent_models,
        )

        struct_logger.info(
            "Cost breakdown",
            session_id=self.session_id,
            cycle=self.cycle_num,
            total_cost_usd=breakdown.total_cost_usd,
            total_tokens=breakdown.total_tokens,
            agent_summary=breakdown.agent_summary,
        )
        return breakdown

    def reset_cycle(self) -> None:
        """Reset usage tracker for next cycle."""
        self.usage_tracker = UsageTracker()
