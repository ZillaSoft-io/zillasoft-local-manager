"""Phase 2b — the dry-run validation handshake (spec §3.2).

Flow:
  1. Sonnet drafts a dry-run plan from the task context.
  2. Haiku validates it against Mario's original intent.
  3. On mismatch, Haiku's corrections go back to Sonnet, which revises.
     Repeat up to `max_rounds`.
  4. Only the validated plan is turned into final instructions for Opus.

This catches misunderstood requirements at near-zero cost (Haiku + Sonnet are
cheap) before Opus burns tokens on the wrong approach.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .haiku import HaikuAgent, ValidationVerdict
from .sonnet import SonnetAgent

logger = logging.getLogger(__name__)


@dataclass
class DryRunResult:
    plan: str
    instructions: str
    approved: bool
    rounds: int
    verdicts: list[ValidationVerdict] = field(default_factory=list)


def run_dry_run(sonnet: SonnetAgent, haiku: HaikuAgent, *,
                context: str, original_intent: str,
                max_rounds: int = 3) -> DryRunResult:
    """Run the Sonnet→Haiku dry-run loop, then produce Opus instructions."""
    plan = sonnet.generate_dry_run_plan(context)
    verdicts: list[ValidationVerdict] = []
    approved = False
    rounds = 0

    for rounds in range(1, max_rounds + 1):
        verdict = haiku.validate_dry_run_plan(original_intent, plan)
        verdicts.append(verdict)
        if verdict.approved:
            approved = True
            logger.info("Dry-run plan approved on round %d.", rounds)
            break
        logger.info("Dry-run plan rejected (round %d): %s",
                    rounds, verdict.corrections)
        plan = sonnet.revise_dry_run_plan(context, plan, verdict.corrections)

    # Generate instructions only from an approved plan. If we exhausted rounds
    # without approval, the caller (orchestrator) escalates to Mario; we still
    # return the best plan and an empty instruction set.
    instructions = ""
    if approved:
        instructions = sonnet.generate_instructions(context, plan)

    return DryRunResult(
        plan=plan,
        instructions=instructions,
        approved=approved,
        rounds=rounds,
        verdicts=verdicts,
    )
