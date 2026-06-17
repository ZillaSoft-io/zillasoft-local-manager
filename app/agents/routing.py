"""Intelligent agent routing based on task complexity.

After Sonnet generates a plan, analyze it to determine if Haiku can implement it
(saving ~70% on expensive Opus calls) or if Opus is needed for complex logic.
"""
from __future__ import annotations

import logging
import re
from enum import Enum

logger = logging.getLogger(__name__)


class RoutingDecision(Enum):
    """Agent selection based on plan analysis."""
    USE_HAIKU = "haiku"      # Simple: rename, comment, config, format
    USE_OPUS = "opus"        # Complex: logic, new functions, bug fixes


# Keywords indicating a simple task suitable for Haiku
HAIKU_INDICATORS = {
    # Variable/naming changes
    "rename", "variable", "method", "function name", "identifier",
    # Documentation
    "comment", "docstring", "documentation", "javadoc", "doc string",
    # Formatting/cleanup
    "format", "indent", "whitespace", "spacing", "reorder", "sort",
    # Metadata/config
    "config", "environment", "setting", "variable", "constant",
    # Text/static content
    "typo", "spelling", "text", "message", "string", "label",
    # Import/module organization
    "import", "export", "module", "package",
    # Simple refactoring (extract, inline at symbol level)
    "extract variable", "inline", "move", "consolidate",
}

# Keywords indicating complex work requiring Opus
OPUS_INDICATORS = {
    # Logic
    "logic", "algorithm", "condition", "if-else", "switch", "loop",
    # Functions/classes
    "function", "method", "class", "interface", "type", "struct",
    # Behavioral change
    "behavior", "handle", "process", "implement", "feature", "capability",
    # Bug fixes
    "bug", "fix", "issue", "crash", "error handling", "exception",
    # API/contract changes
    "api", "endpoint", "route", "signature", "parameter", "return",
    # Complex refactoring
    "refactor", "rewrite", "restructure", "decompose", "extract class",
    # Testing
    "test case", "test", "unit test", "integration test",
    # Data flow
    "flow", "stream", "pipeline", "transform", "validation",
}


def analyze_plan(plan: str) -> RoutingDecision:
    """Analyze Sonnet's plan and decide which agent should implement it.

    Strategy: count indicators in the plan text (case-insensitive).
    If Haiku indicators > Opus indicators, prefer Haiku.
    Otherwise (or on tie), use Opus (safer choice for complex work).

    Args:
        plan: Sonnet's DRY-RUN PLAN text

    Returns:
        RoutingDecision.USE_HAIKU or RoutingDecision.USE_OPUS
    """
    plan_lower = plan.lower()

    # Count indicators
    haiku_score = sum(
        len(re.findall(rf'\b{re.escape(indicator)}\b', plan_lower))
        for indicator in HAIKU_INDICATORS
    )
    opus_score = sum(
        len(re.findall(rf'\b{re.escape(indicator)}\b', plan_lower))
        for indicator in OPUS_INDICATORS
    )

    decision = (
        RoutingDecision.USE_HAIKU
        if haiku_score > opus_score else
        RoutingDecision.USE_OPUS
    )

    logger.debug(
        f"Plan analysis: Haiku={haiku_score}, Opus={opus_score} → "
        f"{decision.value.upper()}"
    )
    return decision


def should_use_cheap_agent(plan: str) -> bool:
    """Convenience: true if plan analysis suggests Haiku is sufficient."""
    return analyze_plan(plan) == RoutingDecision.USE_HAIKU
