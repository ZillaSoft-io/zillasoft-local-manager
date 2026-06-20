"""Assess how complex a code change is, from its git diff.

Tests are run by the executor (a shell command), not by an agent, so this does
NOT pick a "test runner". It produces a simple/complex signal that drives how
much effort the review step spends (deeper review for complex changes).
"""
from __future__ import annotations

import logging
import re
import subprocess
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ChangeComplexity(str, Enum):
    """Change complexity levels."""
    SIMPLE = "haiku"      # Rename, typo, one-liner fixes
    COMPLEX = "sonnet"    # New features, logic changes, refactoring


class ChangeComplexityAnalyzer:
    """Analyze git diffs to determine test execution complexity."""

    # Simple change patterns (low risk, Haiku can test)
    SIMPLE_PATTERNS = [
        r"^\s*\w+\s*=\s*\w+\s*$",  # Variable assignment
        r"rename\s+\w+\s+to\s+\w+",  # Rename variable/constant
        r"fix\s+typo|correct\s+spelling",  # Typo fixes
        r"update\s+(comment|docstring)",  # Comment updates
        r"^\s*#.*$",  # Comment-only lines
        r"^\s*\+\s*$|^\s*-\s*$",  # Whitespace changes
    ]

    # Complex change patterns (high risk, Sonnet should test)
    COMPLEX_PATTERNS = [
        r"class\s+\w+",  # New class
        r"def\s+\w+",  # New function
        r"async\s+def",  # Async function
        r"import\s+\w+",  # New import
        r"if\s+.*:",  # Conditional logic
        r"for\s+.*in",  # Loop
        r"while\s+",  # While loop
        r"try:|except|finally:",  # Exception handling
        r"lambda",  # Lambda functions
        r"\[.*for.*in.*\]",  # List comprehension
        r"\{.*:.*for.*in.*\}",  # Dict comprehension
        r"raise\s+\w+",  # Exceptions raised
        r"yield",  # Generators
        r"@\w+\s*\(",  # Decorators
    ]

    @staticmethod
    def get_diff(repo_path: str, base_sha: str = "HEAD^") -> str:
        """Get git diff from base to HEAD.

        Args:
            repo_path: path to git repo
            base_sha: base commit to diff from (default: parent of HEAD)

        Returns:
            diff output or empty string if no diff
        """
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "diff", base_sha, "HEAD"],
                capture_output=True, text=True, timeout=10
            )
            return result.stdout if result.returncode == 0 else ""
        except Exception as e:
            logger.warning(f"Failed to get diff: {e}")
            return ""

    @staticmethod
    def analyze_diff(diff_output: str) -> ChangeComplexity:
        """Analyze diff to determine change complexity.

        Simple changes:
        - Only variable/constant changes
        - Typo fixes
        - Comment updates
        - Whitespace-only changes
        - 1-5 lines changed

        Complex changes:
        - New classes, functions, methods
        - Logic changes (if/for/while/try)
        - New imports
        - Loops, comprehensions, generators
        - Exception handling
        - 10+ lines changed

        Args:
            diff_output: git diff output

        Returns:
            ChangeComplexity.SIMPLE or ChangeComplexity.COMPLEX
        """
        if not diff_output.strip():
            logger.debug("No changes detected → Simple (no tests needed)")
            return ChangeComplexity.SIMPLE

        lines = diff_output.split("\n")
        added_lines = [l for l in lines if l.startswith("+") and not l.startswith("+++")]
        removed_lines = [l for l in lines if l.startswith("-") and not l.startswith("---")]

        # Changed lines count (minus metadata lines)
        changed_count = len(added_lines) + len(removed_lines)

        # Check for complex patterns in the diff
        diff_text = diff_output.lower()

        complex_pattern_found = any(
            re.search(pattern, diff_text, re.MULTILINE)
            for pattern in ChangeComplexityAnalyzer.COMPLEX_PATTERNS
        )

        simple_pattern_found = all(
            any(re.search(pattern, line) for pattern in ChangeComplexityAnalyzer.SIMPLE_PATTERNS)
            for line in added_lines + removed_lines
            if line.strip() and not line.startswith("+++") and not line.startswith("---")
        ) if added_lines or removed_lines else False

        # Decision logic
        if complex_pattern_found:
            logger.debug(
                f"Complex patterns detected in diff ({changed_count} lines) "
                "→ routing to Sonnet"
            )
            return ChangeComplexity.COMPLEX

        if changed_count <= 5 and not any(
            re.search(r"def\s+\w+|class\s+\w+|import\s+\w+", diff_text)
            for pattern in [r"def\s+\w+", r"class\s+\w+", r"import\s+\w+"]
        ):
            logger.debug(
                f"Simple changes only ({changed_count} lines) "
                "→ routing to Haiku"
            )
            return ChangeComplexity.SIMPLE

        # Default to Sonnet for safety on larger/uncertain changes
        logger.debug(
            f"Unable to determine complexity ({changed_count} lines, "
            "conservative routing) → Sonnet"
        )
        return ChangeComplexity.COMPLEX

    @staticmethod
    def assess_complexity(diff_output: str) -> str:
        """Classify the change as 'simple' or 'complex' (drives review depth)."""
        complexity = ChangeComplexityAnalyzer.analyze_diff(diff_output)
        return "simple" if complexity == ChangeComplexity.SIMPLE else "complex"


# Global analyzer instance
_analyzer: ChangeComplexityAnalyzer | None = None


def get_change_analyzer() -> ChangeComplexityAnalyzer:
    """Get or create global change complexity analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = ChangeComplexityAnalyzer()
    return _analyzer
