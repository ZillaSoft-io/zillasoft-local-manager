"""Smart retry logic: refine instructions instead of regenerating on test failure.

When tests fail, analyze the failure and refine the implementation instructions
instead of generating entirely new ones. Saves 30-40% on retry costs.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SmartRetryAnalyzer:
    """Analyze test failures and generate refined instructions."""

    @staticmethod
    def should_refine_instead_of_regenerate(test_output: str, failure_count: int) -> bool:
        """Determine if we should refine vs regenerate.

        Refine when:
        - Single specific assertion failed (not cascading failures)
        - Error is clear and actionable
        - First or second attempt (not too many retries)

        Regenerate when:
        - Multiple different failures
        - Vague or cascading errors
        - Already retried 2+ times with no progress

        Returns:
            True if should refine, False if should regenerate
        """
        if failure_count >= 3:
            # Too many retries, give up on refinement
            logger.debug("Too many failures, regenerating from scratch")
            return False

        # Check for specific actionable errors
        actionable_patterns = [
            "AssertionError",
            "expected",
            "got",
            "TypeError",
            "AttributeError",
            "KeyError",
            "IndexError",
        ]

        # Check for cascading/vague errors
        vague_patterns = [
            "timeout",
            "hang",
            "crash",
            "segmentation",
            "memory",
            "deadlock",
            "race",
            "flaky",
        ]

        has_actionable = any(p in test_output for p in actionable_patterns)
        has_vague = any(p in test_output for p in vague_patterns)

        if has_vague:
            logger.debug("Vague error detected, regenerating")
            return False

        if has_actionable and not has_vague:
            logger.debug("Specific error detected, refining instructions")
            return True

        # Default: if only one failure, try refining first
        return failure_count == 1

    @staticmethod
    def generate_refinement_prompt(
        original_instructions: str,
        test_output: str,
        test_summary: str
    ) -> str:
        """Generate a refinement prompt instead of new instructions.

        Instead of "implement X", say "fix the issue in your implementation".
        Much cheaper and more targeted.

        Returns:
            Refinement prompt for Sonnet
        """
        return f"""The previous implementation failed tests. Here's what happened:

**Test Summary:** {test_summary}

**Failed Test Output:**
{test_output}

**Original Instructions:**
{original_instructions}

**Your task:**
Analyze the test failure above and refine your implementation to fix it.
- Don't rewrite everything—just fix the specific issue
- Keep the overall approach the same
- Make the minimal change to pass the failing tests

Focus on: what does the test expect vs what did you provide?"""

    @staticmethod
    def cost_savings(failure_count: int, refine_not_regenerate: bool) -> dict:
        """Calculate cost savings from smart retry.

        Args:
            failure_count: which retry attempt this is
            refine_not_regenerate: whether using refine (vs regenerate)

        Returns:
            dict with cost comparison
        """
        # Regenerate costs (full plan + implementation)
        regenerate_cost = 0.0020 + 0.0080  # Sonnet plan + Opus impl

        # Refine costs (just refinement analysis + impl)
        refine_cost = 0.0010 + 0.0050  # Sonnet analysis + Opus refine

        savings = regenerate_cost - refine_cost
        savings_percent = (savings / regenerate_cost) * 100

        return {
            "regenerate_cost": regenerate_cost,
            "refine_cost": refine_cost,
            "savings_per_retry": round(savings, 4),
            "savings_percent": round(savings_percent, 1),
            "total_savings_if_used": round(savings * failure_count, 4),
        }


def should_retry_with_refinement(
    test_output: str,
    original_instructions: str,
    failure_count: int
) -> tuple[bool, str]:
    """Decide whether to refine vs regenerate, and return prompt if refining.

    Returns:
        (should_refine, prompt_or_regenerate_signal)
    """
    analyzer = SmartRetryAnalyzer()

    if analyzer.should_refine_instead_of_regenerate(test_output, failure_count):
        prompt = analyzer.generate_refinement_prompt(
            original_instructions,
            test_output,
            "Previous attempt failed"
        )
        return (True, prompt)
    else:
        return (False, "regenerate_from_scratch")
