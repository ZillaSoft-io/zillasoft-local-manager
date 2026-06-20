"""Haiku — orchestrator & input handler (spec §3.1, §3.2 dry-run validation)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .base import Agent
from .prompts import HAIKU_SYSTEM

logger = logging.getLogger(__name__)

# JSON schema for the dry-run validation verdict (Haiku 4.5 supports
# structured outputs).
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "corrections": {"type": "string"},
    },
    "required": ["approved", "corrections"],
    "additionalProperties": False,
}


@dataclass
class ValidationVerdict:
    approved: bool
    corrections: str = ""


# Structured output for implementation-complexity routing. Haiku judges how hard
# the task is and names the cheapest model that can do it reliably.
_COMPLEXITY_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string", "enum": ["haiku", "sonnet", "opus"]},
        "reason": {"type": "string"},
    },
    "required": ["agent", "reason"],
    "additionalProperties": False,
}


# Structured output for one clarification turn. Haiku either asks another
# question (status="asking") or signals it has enough context (status="ready"),
# in which case it fills the compiled summary, scope, and (for new apps) a
# recommended stack.
_CLARIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["asking", "ready"]},
        "message": {"type": "string"},
        "context_summary": {"type": "string"},
        "scope_level": {"type": "string", "enum": ["capped", "uncapped", ""]},
        "monthly_cap": {"type": "number"},
        "recommended_stack": {"type": "string"},
        "app_name": {"type": "string"},
    },
    "required": ["status", "message", "context_summary", "scope_level",
                 "monthly_cap", "recommended_stack", "app_name"],
    "additionalProperties": False,
}


@dataclass
class ClarifyTurn:
    status: str            # "asking" | "ready"
    message: str           # question for Mario, or ready confirmation
    context_summary: str = ""
    scope_level: str = ""
    monthly_cap: float = 0.0
    recommended_stack: str = ""
    app_name: str = ""


class HaikuAgent(Agent):
    label = "haiku"
    system_prompt = HAIKU_SYSTEM
    model_key = "ANTHROPIC_MODEL_HAIKU"
    effort_key = "ANTHROPIC_EFFORT_HAIKU"

    def validate_dry_run_plan(self, original_intent: str,
                              plan: str) -> ValidationVerdict:
        """Compare Sonnet's dry-run plan against Mario's original intent.

        Returns approved=True if aligned, else approved=False with specific
        corrections for Sonnet. Haiku does not support adaptive thinking, so we
        disable it; structured output keeps the verdict machine-readable.
        """
        prompt = (
            "Mario's original request and clarifications:\n"
            f"{original_intent}\n\n"
            "Sonnet's proposed dry-run plan:\n"
            f"{plan}\n\n"
            "Does the plan match Mario's actual intent and scope? If it drifts "
            "(touches systems he didn't ask about, misunderstands the goal, or "
            "over-/under-reaches), set approved=false and give specific, "
            "actionable corrections. If it's aligned, set approved=true and "
            "leave corrections empty."
        )
        resp = self.ask(
            prompt,
            thinking=False,  # Haiku 4.5 has no adaptive thinking
            output_config={"format": {"type": "json_schema",
                                      "schema": _VERDICT_SCHEMA}},
        )
        data = self._parse_json(resp.text)
        return ValidationVerdict(
            approved=bool(data.get("approved", False)),
            corrections=str(data.get("corrections", "")).strip(),
        )

    def classify_complexity(self, context: str, plan: str) -> tuple[str, str]:
        """Pick the cheapest model that can implement this task reliably.

        Tiers:
          haiku  - trivial mechanical edits (typo, comment, config value, text/
                   string change, simple rename); no real logic.
          sonnet - moderate work (small feature, refactor, multi-file edit,
                   straightforward bug fix).
          opus   - complex work (non-trivial logic, new subsystem, tricky bug,
                   anything needing careful multi-step reasoning).

        Returns (agent_label, reason). Conservative by design: when uncertain it
        is told to pick the MORE capable model, since under-powering a task costs
        more (extra cycles) than the model itself. Falls back to "opus" if the
        response is malformed.
        """
        prompt = (
            "Route an implementation task to the cheapest model that can do it "
            "RELIABLY. Tiers:\n"
            "- haiku: trivial mechanical edits (fix a typo, edit a comment, "
            "change a config value, rename, simple text/string change). No logic.\n"
            "- sonnet: moderate work (small feature, refactor, multi-file edit, "
            "straightforward bug fix).\n"
            "- opus: complex work (non-trivial logic, new subsystem, tricky bug, "
            "careful multi-step reasoning).\n\n"
            "When uncertain, choose the MORE capable model.\n\n"
            f"Task context:\n{context}\n\nPlan:\n{plan}\n\n"
            "Pick the agent and give a one-line reason."
        )
        resp = self.ask(
            prompt,
            thinking=False,  # Haiku 4.5 has no adaptive thinking
            output_config={"format": {"type": "json_schema",
                                      "schema": _COMPLEXITY_SCHEMA}},
        )
        data = self._parse_json(resp.text)
        agent = str(data.get("agent", "opus")).lower().strip()
        if agent not in ("haiku", "sonnet", "opus"):
            agent = "opus"
        return agent, str(data.get("reason", "")).strip()

    # ------------------------------------------------------------------ #
    # Phase 3 — input clarification
    # ------------------------------------------------------------------ #
    def clarify(self, *, clarify_instructions: str,
                messages: list[dict]) -> ClarifyTurn:
        """One clarification turn over the running transcript.

        `clarify_instructions` is appended to Haiku's system prompt and carries
        the task type, required fields, and any fetched Sentry/Jira context.
        `messages` is the conversation so far (ends with Mario's latest turn).
        Returns a structured turn (ask another question, or ready-with-context).
        """
        resp = self.client.complete(
            model=self.model,
            system=self.system_prompt + "\n\n" + clarify_instructions,
            messages=messages,
            effort=self.effort,
            thinking=False,  # Haiku 4.5: no adaptive thinking
            output_config={"format": {"type": "json_schema",
                                      "schema": _CLARIFY_SCHEMA}},
            agent_label=self.label,
        )
        data = self._parse_json(resp.text)
        return ClarifyTurn(
            status=str(data.get("status", "asking")),
            message=str(data.get("message", "")),
            context_summary=str(data.get("context_summary", "")),
            scope_level=str(data.get("scope_level", "")),
            monthly_cap=float(data.get("monthly_cap", 0) or 0),
            recommended_stack=str(data.get("recommended_stack", "")),
            app_name=str(data.get("app_name", "")),
        )
