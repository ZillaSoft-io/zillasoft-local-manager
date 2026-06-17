"""Better escalation messages: provide context about why escalation happened.

Helps users understand the failure and next steps.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class EscalationMessageBuilder:
    """Build detailed escalation messages with context."""

    @staticmethod
    def plan_rejected(error: str, cycles_attempted: int = 1) -> str:
        """Plan validation rejected."""
        return (
            f"Plan was not approved after {cycles_attempted} validation cycle(s).\n"
            f"Error: {error}\n\n"
            f"**Next steps:**\n"
            f"- Review the plan above\n"
            f"- Clarify the requirements\n"
            f"- Haiku may have misunderstood the intent"
        )

    @staticmethod
    def test_crash(cycle: int, error: str) -> str:
        """Test execution crashed."""
        return (
            f"**Cycle {cycle}: Test execution crashed**\n"
            f"Error: {error}\n\n"
            f"Progress was saved to checkpoint before crash.\n"
            f"Cycle data is available in audit trail.\n\n"
            f"**Next steps:**\n"
            f"- Check the error message above\n"
            f"- Review what Opus changed in this cycle\n"
            f"- Retry cycle {cycle} after investigating"
        )

    @staticmethod
    def test_failed_pattern(error_msg: str, cycle: int, max_cycles: int) -> str:
        """Test failed with known pattern (seen before)."""
        return (
            f"**Cycle {cycle}: Test failure matches a known pattern**\n"
            f"Error: {error_msg}\n\n"
            f"This error has been seen {2}+ times before (likely a fundamental issue, not transient).\n"
            f"Retrying may not help.\n\n"
            f"**What happened:**\n"
            f"- Opus made a change\n"
            f"- Tests failed the same way as before\n"
            f"- Feedback loop detected this pattern\n\n"
            f"**Next steps:**\n"
            f"- Review the test output above\n"
            f"- Check if the issue is in the test or the implementation\n"
            f"- Clarify requirements if there's ambiguity"
        )

    @staticmethod
    def max_cycles_reached(max_cycles: int, last_failure: str) -> str:
        """Reached max cycle limit without passing tests."""
        return (
            f"**Reached cycle limit: {max_cycles} cycles without passing tests**\n\n"
            f"Last failure: {last_failure}\n\n"
            f"**What happened:**\n"
            f"- Opus made changes and ran tests {max_cycles} times\n"
            f"- Tests still failing\n"
            f"- Reached retry limit\n\n"
            f"**Next steps:**\n"
            f"- Simplify the task (break into smaller pieces)\n"
            f"- Clarify requirements\n"
            f"- Provide additional context or constraints\n"
            f"- Review the test output to understand what's broken"
        )

    @staticmethod
    def budget_exceeded(current: float, cap: float) -> str:
        """Budget limit reached."""
        return (
            f"**Monthly budget exceeded**\n\n"
            f"Current spend: ${current:.2f}\n"
            f"Monthly cap: ${cap:.2f}\n\n"
            f"Cannot accept new tasks until next billing cycle."
        )

    @staticmethod
    def unexpected_error(error_type: str, error_msg: str) -> str:
        """Unexpected error occurred."""
        return (
            f"**Unexpected error: {error_type}**\n"
            f"Message: {error_msg}\n\n"
            f"This is likely a system error, not a task failure.\n\n"
            f"**Debug info:**\n"
            f"- Check system logs\n"
            f"- Verify all services are running\n"
            f"- Contact support with this error message"
        )


def build_escalation_reason(reason: str, context: dict = None) -> str:
    """Build a detailed escalation reason from a simple reason code.

    Args:
        reason: escalation reason code or message
        context: additional context dict

    Returns:
        Formatted escalation message
    """
    context = context or {}

    if "plan_rejected" in reason.lower():
        return EscalationMessageBuilder.plan_rejected(
            error=context.get("error", "Plan validation failed"),
            cycles_attempted=context.get("cycles", 1)
        )

    if "test_crash" in reason.lower() or "crashed" in reason.lower():
        return EscalationMessageBuilder.test_crash(
            cycle=context.get("cycle", 0),
            error=reason.split(": ", 1)[-1] if ": " in reason else reason
        )

    if "known_pattern" in reason.lower():
        return EscalationMessageBuilder.test_failed_pattern(
            error_msg=context.get("error", "Test failed"),
            cycle=context.get("cycle", 0),
            max_cycles=context.get("max_cycles", 3)
        )

    if "cycle_limit" in reason.lower():
        return EscalationMessageBuilder.max_cycles_reached(
            max_cycles=context.get("max_cycles", 3),
            last_failure=context.get("last_failure", "Unknown")
        )

    if "budget" in reason.lower():
        return EscalationMessageBuilder.budget_exceeded(
            current=context.get("current", 0),
            cap=context.get("cap", 100)
        )

    # For detailed messages, return as-is
    if "\n" in reason:
        return reason

    # Wrap simple messages with context
    return EscalationMessageBuilder.unexpected_error(
        error_type=context.get("error_type", "Unknown"),
        error_msg=reason
    )
