"""Auto-generate commit messages (spec Phase 6).

Uses Haiku to summarize the diff into a conventional one-line message when
available; otherwise falls back to a safe template. Opus already writes its own
messages during the cycle loop — this is for commits the manager makes itself
(finalizing leftover changes on approval, WIP-on-kill).
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def generate_commit_message(diff_stat: str, *, task_summary: str = "",
                            haiku=None, fallback: str = "") -> str:
    fallback = fallback or "chore: automated change via Local Manager"
    if not diff_stat:
        return fallback
    if haiku is None:
        return fallback
    try:
        prompt = (
            "Write a single conventional-commit message (e.g. 'fix: ...', "
            "'feat: ...') for this change. One line, <= 72 chars, no body, no "
            "quotes.\n\n"
            f"Task: {task_summary}\n\nDiff stat:\n{diff_stat}"
        )
        text = haiku.ask(prompt, thinking=False, max_tokens=120).text.strip()
        line = text.splitlines()[0].strip().strip('"').strip()
        return line or fallback
    except Exception as exc:  # never block a commit on message generation
        logger.warning("Commit-message generation failed: %s", exc)
        return fallback
