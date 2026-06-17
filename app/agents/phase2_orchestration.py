"""Phase 2 orchestration: full end-to-end pipeline with optimization.

Replaces run_dry_run with orchestrator that includes:
- Plan generation (Sonnet) with circuit breaker + caching
- Intelligent routing (Haiku vs Opus decision)
- Parallel execution when tasks are independent
- Cost tracking per agent per cycle
- Structured logging with context
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..cache import SessionCache
from ..cost.breakdown import CycleBreakdown
from .haiku import HaikuAgent, ValidationVerdict
from .orchestrator import AgentOrchestrator
from .sonnet import SonnetAgent
from .opus import OpusAgent

logger = logging.getLogger(__name__)


@dataclass
class Phase2Result:
    """Result of Phase 2 orchestration."""
    plan: str
    routing_decision: str                  # "haiku" or "opus"
    implementation: str                    # result from execution
    approved: bool
    rounds: int
    verdicts: list[ValidationVerdict] = field(default_factory=list)
    cost_breakdown: CycleBreakdown | None = None
    success: bool = False
    error: str = ""


def run_phase2_orchestration(
    haiku: HaikuAgent,
    sonnet: SonnetAgent,
    opus: OpusAgent,
    *,
    context: str,
    original_intent: str,
    session_id: str,
    max_rounds: int = 3,
    cache: SessionCache | None = None,
) -> Phase2Result:
    """Phase 2 orchestration with optimization.

    Args:
        haiku, sonnet, opus: agent instances
        context: full task context
        original_intent: Mario's original request (for validation)
        session_id: for structured logging and caching
        max_rounds: max dry-run validation rounds
        cache: session cache (optional)

    Returns:
        Phase2Result with plan, routing decision, implementation, cost
    """
    if cache is None:
        cache = SessionCache()

    # Initialize orchestrator
    orchestrator = AgentOrchestrator(
        haiku=haiku,
        sonnet=sonnet,
        opus=opus,
        cache=cache,
        session_id=session_id,
    )

    result = Phase2Result(
        plan="",
        routing_decision="",
        implementation="",
        approved=False,
        rounds=0,
        verdicts=[],
        cost_breakdown=None,
        success=False,
        error="",
    )

    try:
        # Phase 1: Plan generation (with circuit breaker + caching)
        plan = orchestrator.plan_phase(context)
        if not plan:
            result.error = "Plan generation failed"
            return result
        result.plan = plan

        # Phase 2: Dry-run validation (Haiku checks against intent)
        verdicts: list[ValidationVerdict] = []
        approved = False
        rounds = 0

        for rounds in range(1, max_rounds + 1):
            verdict = haiku.validate_dry_run_plan(original_intent, plan)
            verdicts.append(verdict)
            if verdict.approved:
                approved = True
                logger.info("Plan approved on round %d.", rounds)
                break
            logger.info("Plan rejected (round %d): %s", rounds, verdict.corrections)
            plan = sonnet.revise_dry_run_plan(context, plan, verdict.corrections)

        result.approved = approved
        result.rounds = rounds
        result.verdicts = verdicts

        if not approved:
            result.error = f"Plan not approved after {max_rounds} rounds"
            return result

        # Phase 3: Routing decision
        agent = orchestrator.routing_phase(plan)
        result.routing_decision = agent

        # Phase 4: Generate instructions for implementation
        instructions = sonnet.generate_instructions(context, plan)

        # Phase 5: Execute (route to Haiku or Opus)
        implementation = orchestrator.execution_phase(agent, instructions)
        if not implementation:
            result.error = "Implementation failed"
            return result
        result.implementation = implementation

        # Phase 6: Cost tracking
        cost_breakdown = orchestrator.cost_phase()
        result.cost_breakdown = cost_breakdown

        result.success = True
        return result

    except Exception as e:
        result.error = f"{type(e).__name__}: {str(e)}"
        logger.exception("Phase 2 orchestration failed: %s", result.error)
        return result


def run_phase2_parallel_orchestration(
    haiku: HaikuAgent,
    sonnet: SonnetAgent,
    opus: OpusAgent,
    *,
    context: str,
    original_intent: str,
    session_id: str,
    simple_task_instructions: str,
    complex_task_instructions: str,
    cache: SessionCache | None = None,
) -> Phase2Result:
    """Phase 2 orchestration with parallel execution.

    When Sonnet identifies two independent tasks (one simple, one complex),
    execute them simultaneously via Haiku and Opus.

    Args:
        haiku, sonnet, opus: agent instances
        context: task context
        original_intent: Mario's request
        session_id: for logging
        simple_task_instructions: what Haiku should do
        complex_task_instructions: what Opus should do
        cache: session cache (optional)

    Returns:
        Phase2Result with parallel execution results
    """
    if cache is None:
        cache = SessionCache()

    orchestrator = AgentOrchestrator(
        haiku=haiku,
        sonnet=sonnet,
        opus=opus,
        cache=cache,
        session_id=session_id,
    )

    result = Phase2Result(
        plan="",
        routing_decision="parallel",
        implementation="",
        approved=True,
        rounds=0,
        cost_breakdown=None,
        success=False,
        error="",
    )

    try:
        # Run Haiku and Opus in parallel
        haiku_result, opus_result = orchestrator.parallel_execution_phase(
            simple_task_instructions,
            complex_task_instructions,
        )

        if not haiku_result or not opus_result:
            result.error = "One or both parallel tasks failed"
            return result

        # Combine results
        result.implementation = f"Haiku:\n{haiku_result}\n\nOpus:\n{opus_result}"

        # Cost tracking
        cost_breakdown = orchestrator.cost_phase()
        result.cost_breakdown = cost_breakdown

        result.success = True
        return result

    except Exception as e:
        result.error = f"{type(e).__name__}: {str(e)}"
        logger.exception("Parallel orchestration failed: %s", result.error)
        return result
