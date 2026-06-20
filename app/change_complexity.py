"""Assess how complex a code change is, from its git diff.

Tests are run by the executor (a shell command), not by an agent, so this does
NOT pick a "test runner". It produces a simple/complex signal that drives how
much effort the review step spends (deeper review for complex changes).

Designed to never destabilise the pipeline: the scan is bounded (line count and
line length), it only looks at the actually-changed lines, and analyze_diff
never raises — any unexpected error falls back to COMPLEX (the safe,
deeper-review direction).
"""
from __future__ import annotations

import logging
import re
import subprocess
from enum import Enum

logger = logging.getLogger(__name__)


class ChangeComplexity(str, Enum):
    """How involved a code change is (drives review depth)."""
    SIMPLE = "simple"     # rename, typo, comment, small mechanical edit
    COMPLEX = "complex"   # new functions/classes, logic, control flow


# Structural / logic constructs that mark a change as complex. Compiled once
# (this also validates them at import). All linear-time — no nested quantifiers,
# so no catastrophic backtracking even on a long line. Reasonably cross-language
# (Python / JS / TS), since the projects span both.
_COMPLEX_PATTERNS = [re.compile(p) for p in (
    r"\bclass\s+\w+",                    # new class
    r"\bdef\s+\w+",                      # new function/method (py)
    r"\bfunction\b",                     # function (js/ts)
    r"\basync\b",                        # async
    r"\bimport\b|\brequire\s*\(",        # new dependency
    r"\bif\b.*:",                        # conditional
    r"\bfor\b.*\bin\b",                  # loop
    r"\bwhile\b",                        # while loop
    r"\b(try|except|finally|catch)\b",   # exception handling
    r"\blambda\b",                       # lambda
    r"\braise\b|\bthrow\b",              # raised/thrown errors
    r"\byield\b",                        # generators
    r"@\w+\s*\(",                        # decorators
)]


class ChangeComplexityAnalyzer:
    """Assess change complexity from a git diff (simple vs complex)."""

    _MAX_DIFF_LINES = 4000   # cap lines scanned on huge diffs
    _MAX_LINE_LEN = 500      # cap chars scanned per line (constructs appear early)
    _SIMPLE_LINE_LIMIT = 5   # <= this many changed lines + no constructs = simple

    @staticmethod
    def get_diff(repo_path: str, base_sha: str = "HEAD^") -> str:
        """`git diff base_sha HEAD`. Returns "" on any failure (never raises)."""
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "diff", base_sha, "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout if result.returncode == 0 else ""
        except Exception as e:
            logger.warning(f"Failed to get diff: {e}")
            return ""

    @staticmethod
    def _changed_lines(diff_output: str) -> list[str]:
        """The actually-changed (+/-) code lines, marker stripped and bounded.

        Excludes diff headers (+++/---) and unchanged context lines, which would
        otherwise falsely trip the complex patterns.
        """
        out: list[str] = []
        for line in diff_output.split("\n")[:ChangeComplexityAnalyzer._MAX_DIFF_LINES]:
            if line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
                out.append(line[1:1 + ChangeComplexityAnalyzer._MAX_LINE_LEN])
        return out

    @staticmethod
    def analyze_diff(diff_output: str) -> ChangeComplexity:
        """Classify the change as SIMPLE or COMPLEX.

        Robust by construction: bounded line count and line length, scans only
        the changed lines, and never raises — any unexpected error defaults to
        COMPLEX so the heuristic can never break the pipeline.
        """
        if not diff_output or not diff_output.strip():
            return ChangeComplexity.SIMPLE
        try:
            changed = ChangeComplexityAnalyzer._changed_lines(diff_output)
            if not changed:
                return ChangeComplexity.SIMPLE
            text = "\n".join(changed)
            if any(p.search(text) for p in _COMPLEX_PATTERNS):
                return ChangeComplexity.COMPLEX
            if len(changed) <= ChangeComplexityAnalyzer._SIMPLE_LINE_LIMIT:
                return ChangeComplexity.SIMPLE
            # Larger change with no obvious construct: be conservative.
            return ChangeComplexity.COMPLEX
        except Exception as e:
            logger.warning(
                "Change-complexity analysis failed (%s); assuming complex.", e)
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
