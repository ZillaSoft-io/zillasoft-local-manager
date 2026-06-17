"""Intelligent test routing: route to Haiku or Sonnet based on test complexity.

Simple tests (all pass, no failures) → Haiku (cheap, fast)
Complex tests (failures, edge cases) → Sonnet (more capable analysis)
"""
from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class TestComplexity(str, Enum):
    """Test complexity levels."""
    SIMPLE = "haiku"      # All tests pass, straightforward
    COMPLEX = "sonnet"    # Failures, edge cases, intricate logic


class TestComplexityAnalyzer:
    """Analyze test results to determine which AI should review them."""

    @staticmethod
    def analyze(test_output: str, test_passed: bool) -> TestComplexity:
        """Analyze test output and determine complexity.

        Simple (Haiku):
        - All tests pass
        - No failures or errors
        - Straightforward output

        Complex (Sonnet):
        - Test failures
        - Timeouts or hangs
        - Multiple failure types
        - Flaky tests
        - Complex error messages
        - Edge case failures

        Args:
            test_output: output from test run (stdout/stderr)
            test_passed: whether all tests passed

        Returns:
            TestComplexity.SIMPLE or TestComplexity.COMPLEX
        """
        # If all tests pass, it's simple
        if test_passed:
            logger.debug("All tests passed → routing to Haiku (simple)")
            return TestComplexity.SIMPLE

        # Test failed — analyze the output to gauge complexity
        output_lower = (test_output or "").lower()

        # High-complexity indicators
        complex_patterns = [
            "timeout",
            "deadlock",
            "race condition",
            "flaky",
            "intermittent",
            "segmentation fault",
            "memory leak",
            "exception",
            "stack overflow",
            "hanging",
            "frozen",
        ]

        # Count failure types (multiple failures = more complex)
        failure_patterns = [
            "assert",
            "fail",
            "error:",
            "failed",
            "traceback",
        ]

        failure_count = sum(1 for p in failure_patterns if p in output_lower)

        # Decision logic
        has_complex_pattern = any(p in output_lower for p in complex_patterns)
        multiple_failures = failure_count >= 2

        if has_complex_pattern or multiple_failures:
            logger.debug(
                f"Complex test failure detected "
                f"(patterns={has_complex_pattern}, multiple_failures={multiple_failures}) "
                f"→ routing to Sonnet"
            )
            return TestComplexity.COMPLEX

        # Single failure with no complex patterns = still analyzable by Haiku
        logger.debug("Single straightforward failure → routing to Haiku")
        return TestComplexity.SIMPLE

    @staticmethod
    def get_routed_agent(test_output: str, test_passed: bool, haiku, sonnet):
        """Get the appropriate testing agent based on complexity.

        Args:
            test_output: test run output
            test_passed: whether tests passed
            haiku: Haiku agent instance
            sonnet: Sonnet agent instance

        Returns:
            Agent instance (haiku or sonnet)
        """
        complexity = TestComplexityAnalyzer.analyze(test_output, test_passed)

        if complexity == TestComplexity.SIMPLE:
            logger.info("Routing test review to Haiku (simple test failure)")
            return haiku
        else:
            logger.info("Routing test review to Sonnet (complex test failure)")
            return sonnet


# Global analyzer instance
_analyzer: TestComplexityAnalyzer | None = None


def get_test_analyzer() -> TestComplexityAnalyzer:
    """Get or create global test complexity analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = TestComplexityAnalyzer()
    return _analyzer
