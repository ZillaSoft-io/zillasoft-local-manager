"""Sonnet — requirement parser, reviewer, summarizer (spec §3.2)."""
from __future__ import annotations

import logging
from typing import Optional

from .base import Agent
from .payload import PAYLOAD_TOKEN_LIMIT, enforce_payload_limit
from .prompts import SONNET_SYSTEM
from .tokens import TokenCounter

logger = logging.getLogger(__name__)


class SonnetAgent(Agent):
    label = "sonnet"
    system_prompt = SONNET_SYSTEM
    model_key = "ANTHROPIC_MODEL_SONNET"
    effort_key = "ANTHROPIC_EFFORT_SONNET"

    # ------------------------------------------------------------------ #
    # Phase 2b — dry-run plan
    # ------------------------------------------------------------------ #
    def generate_dry_run_plan(self, context: str) -> str:
        prompt = (
            "Produce a concise DRY-RUN PLAN for this task. List: files to "
            "modify/create, the logic changes and why, tests to validate, and "
            "risks/edge cases. Do not write code yet.\n\n"
            f"Task context:\n{context}"
        )
        return self.ask(prompt).text

    def revise_dry_run_plan(self, context: str, previous_plan: str,
                            corrections: str) -> str:
        prompt = (
            "Revise your dry-run plan to address Haiku's corrections.\n\n"
            f"Task context:\n{context}\n\n"
            f"Your previous plan:\n{previous_plan}\n\n"
            f"Haiku's corrections (align to these):\n{corrections}\n\n"
            "Return the corrected dry-run plan only."
        )
        return self.ask(prompt).text

    # ------------------------------------------------------------------ #
    # Phase 2b — final instructions for Opus
    # ------------------------------------------------------------------ #
    def generate_instructions(self, context: str, validated_plan: str) -> str:
        prompt = (
            "The dry-run plan below was validated by Haiku against Mario's "
            "intent. Write clear, actionable INSTRUCTIONS for Opus: exactly "
            "what to change (files, logic), what NOT to touch, tests to run, "
            "and edge cases. Keep it under 8000 tokens.\n\n"
            f"Task context:\n{context}\n\n"
            f"Validated plan:\n{validated_plan}"
        )
        resp = self.ask(prompt)
        # Instructions become an inter-agent payload — keep them in budget.
        return enforce_payload_limit(
            resp.text,
            counter=TokenCounter(),
            reducer=lambda text, limit: self._compress(text, limit),
        )

    # ------------------------------------------------------------------ #
    # Summarize Opus's output before passing forward (spec §1)
    # ------------------------------------------------------------------ #
    def summarize_opus_output(self, opus_output: str,
                              limit: int = PAYLOAD_TOKEN_LIMIT) -> str:
        prompt = (
            "Summarize Opus's output below for the next step. Keep only key "
            "outputs (changed files, commit info, key reasoning). Must stay "
            f"under {limit} tokens. If it would exceed that, prioritize: error "
            "first, changed files second, reasoning last.\n\n"
            f"Opus output:\n{opus_output}"
        )
        resp = self.ask(prompt)
        return enforce_payload_limit(
            resp.text,
            limit=limit,
            counter=TokenCounter(),
            reducer=lambda text, lim: self._compress(text, lim),
        )

    # ------------------------------------------------------------------ #
    # Phase 5 — review test results, derive the next task on failure
    # ------------------------------------------------------------------ #
    def review_after_tests(self, *, opus_summary: str, test_summary: str,
                           passed: bool) -> str:
        prompt = (
            "Review the change against the test results in 2-4 sentences.\n\n"
            f"What Opus did:\n{opus_summary}\n\n"
            f"Test result: {'PASSED' if passed else 'FAILED'} — {test_summary}"
        )
        return self.ask(prompt, max_tokens=1000).text

    def bug_from_failure(self, *, instructions: str, test_output: str) -> str:
        """Turn a test failure into a focused NEW task for Opus (not a retry)."""
        prompt = (
            "The tests failed after Opus's change. Write a focused instruction "
            "for Opus to fix THIS new failure (treat it as a separate bug, not "
            "a retry of the whole task). Reference the specific error.\n\n"
            f"Original instructions:\n{instructions}\n\n"
            f"Test output (tail):\n{test_output[-4000:]}"
        )
        return enforce_payload_limit(
            self.ask(prompt).text, counter=TokenCounter(),
            reducer=lambda t, lim: self._compress(t, lim))

    def _compress(self, text: str, limit: int) -> str:
        """Ask Sonnet to re-summarize more tightly to fit `limit` tokens."""
        resp = self.ask(
            "This summary is over budget. Re-summarize it to fit within "
            f"{limit} tokens, dropping the least important details first "
            "(reasoning before changed files before errors):\n\n" + text
        )
        return resp.text
