"""Base agent: identity (model/effort/label) plus ALL pipeline skills.

Every skill lives here and uses a TASK-specific system prompt rather than the
agent's identity prompt, so any agent (Haiku/Sonnet/Opus/future models) can
perform any task. That is what makes complexity routing and cross-agent fallback
real: a task routed to — or failing over to — a different model still runs with
the correct task instructions, just on a different-strength model.

The thin subclasses in haiku.py / sonnet.py / opus.py only set identity
(label, model_key, effort_key) and re-export the dataclasses for compatibility.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .client import AgentResponse, AnthropicClient
from .payload import PAYLOAD_TOKEN_LIMIT, enforce_payload_limit
from .prompts import IMPLEMENT_SYSTEM, ORCHESTRATE_SYSTEM, PLAN_SYSTEM
from .tokens import TokenCounter

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Structured results / schemas (shared across all agents)
# --------------------------------------------------------------------------- #
@dataclass
class ValidationVerdict:
    approved: bool
    corrections: str = ""


@dataclass
class ClarifyTurn:
    status: str            # "asking" | "ready"
    message: str           # question for Mario, or ready confirmation
    context_summary: str = ""
    scope_level: str = ""
    monthly_cap: float = 0.0
    recommended_stack: str = ""
    app_name: str = ""
    task_type: str = ""    # detected: bug_fix | feature | new_app


@dataclass
class ImplementResult:
    text: str                       # the implementer's final message
    commands: list[str] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False           # cancelled via kill/pause signal
    last_commit_sha: Optional[str] = None


# Backwards-compatible alias (older code referred to OpusResult).
OpusResult = ImplementResult


_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "corrections": {"type": "string"},
    },
    "required": ["approved", "corrections"],
    "additionalProperties": False,
}

_COMPLEXITY_SCHEMA = {
    "type": "object",
    "properties": {
        "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
        "effort": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
    },
    "required": ["complexity", "effort", "reason"],
    "additionalProperties": False,
}

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
        "task_type": {"type": "string",
                      "enum": ["bug_fix", "feature", "new_app", ""]},
    },
    "required": ["status", "message", "context_summary", "scope_level",
                 "monthly_cap", "recommended_stack", "app_name", "task_type"],
    "additionalProperties": False,
}

_BASH_TOOL = {
    "name": "run_bash",
    "description": (
        "Run a bash command in the target repository's working directory. "
        "Use it to read files, write/edit code (heredocs are fine), run git, "
        "and commit locally. Do NOT push — commits are local only. The working "
        "directory persists across calls within a session."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


class Agent:
    """Identity + all pipeline skills. Subclasses set only label/model/effort."""

    #: subclasses set these
    label: str = "agent"
    system_prompt: str = ""   # identity default for a bare ask(); skills override
    model_key: str = ""       # e.g. "ANTHROPIC_MODEL_SONNET"
    effort_key: str = ""      # e.g. "ANTHROPIC_EFFORT_SONNET"

    def __init__(self, client: AnthropicClient, config):
        self.client = client
        self.config = config

    @property
    def model(self) -> str:
        return self.config.get_raw(self.model_key)

    @property
    def effort(self) -> Optional[str]:
        return self.config.get_raw(self.effort_key)

    def ask(self, user_content: str, *, max_tokens: int = 8000,
            thinking: bool = True, output_config: Optional[dict] = None,
            stream: bool = False, system: Optional[str] = None) -> AgentResponse:
        """Single-turn call. `system` selects the TASK prompt; defaults to this
        agent's identity prompt. Per-model `thinking`/`effort` are gated by the
        client, so any task is safe to run on any model."""
        return self.client.complete(
            model=self.model,
            system=system if system is not None else self.system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens,
            effort=self.effort,
            thinking=thinking,
            output_config=output_config,
            stream=stream,
            agent_label=self.label,
        )

    # ------------------------------------------------------------------ #
    # Planning / requirement skills (task prompt: PLAN_SYSTEM)
    # ------------------------------------------------------------------ #
    def generate_dry_run_plan(self, context: str) -> str:
        prompt = (
            "Produce a concise DRY-RUN PLAN for this task. List: files to "
            "modify/create, the logic changes and why, tests to validate, and "
            "risks/edge cases. Do not write code yet.\n\n"
            f"Task context:\n{context}"
        )
        return self.ask(prompt, system=PLAN_SYSTEM).text

    def revise_dry_run_plan(self, context: str, previous_plan: str,
                            corrections: str) -> str:
        prompt = (
            "Revise your dry-run plan to address the validator's corrections.\n\n"
            f"Task context:\n{context}\n\n"
            f"Your previous plan:\n{previous_plan}\n\n"
            f"Corrections (align to these):\n{corrections}\n\n"
            "Return the corrected dry-run plan only."
        )
        return self.ask(prompt, system=PLAN_SYSTEM).text

    def generate_instructions(self, context: str, validated_plan: str) -> str:
        prompt = (
            "The dry-run plan below was validated against Mario's intent. Write "
            "clear, actionable INSTRUCTIONS for the implementer: exactly what to "
            "change (files, logic), what NOT to touch, tests to run, and edge "
            "cases. Keep it under 8000 tokens.\n\n"
            f"Task context:\n{context}\n\n"
            f"Validated plan:\n{validated_plan}"
        )
        resp = self.ask(prompt, system=PLAN_SYSTEM)
        return enforce_payload_limit(
            resp.text,
            counter=TokenCounter(),
            reducer=lambda text, limit: self._compress(text, limit),
        )

    def summarize_opus_output(self, opus_output: str,
                              limit: int = PAYLOAD_TOKEN_LIMIT) -> str:
        prompt = (
            "Summarize the implementer's output below for the next step. Keep "
            "only key outputs (changed files, commit info, key reasoning). Must "
            f"stay under {limit} tokens. If it would exceed that, prioritize: "
            "error first, changed files second, reasoning last.\n\n"
            f"Implementer output:\n{opus_output}"
        )
        resp = self.ask(prompt, system=PLAN_SYSTEM)
        return enforce_payload_limit(
            resp.text,
            limit=limit,
            counter=TokenCounter(),
            reducer=lambda text, lim: self._compress(text, lim),
        )

    def review_after_tests(self, *, opus_summary: str, test_summary: str,
                           passed: bool) -> str:
        prompt = (
            "Review the change against the test results in 2-4 sentences.\n\n"
            f"What the implementer did:\n{opus_summary}\n\n"
            f"Test result: {'PASSED' if passed else 'FAILED'} — {test_summary}"
        )
        return self.ask(prompt, max_tokens=1000, system=PLAN_SYSTEM).text

    def bug_from_failure(self, *, instructions: str, test_output: str) -> str:
        """Turn a test failure into a focused NEW task (not a retry)."""
        prompt = (
            "The tests failed after the change. Write a focused instruction to "
            "fix THIS new failure (treat it as a separate bug, not a retry of "
            "the whole task). Reference the specific error.\n\n"
            f"Original instructions:\n{instructions}\n\n"
            f"Test output (tail):\n{test_output[-4000:]}"
        )
        return enforce_payload_limit(
            self.ask(prompt, system=PLAN_SYSTEM).text, counter=TokenCounter(),
            reducer=lambda t, lim: self._compress(t, lim))

    def _compress(self, text: str, limit: int) -> str:
        """Re-summarize more tightly to fit `limit` tokens."""
        resp = self.ask(
            "This summary is over budget. Re-summarize it to fit within "
            f"{limit} tokens, dropping the least important details first "
            "(reasoning before changed files before errors):\n\n" + text,
            system=PLAN_SYSTEM,
        )
        return resp.text

    # ------------------------------------------------------------------ #
    # Orchestration skills: validation + complexity (task: ORCHESTRATE_SYSTEM)
    # ------------------------------------------------------------------ #
    def validate_dry_run_plan(self, original_intent: str,
                              plan: str) -> ValidationVerdict:
        """Compare a dry-run plan against Mario's original intent."""
        prompt = (
            "Mario's original request and clarifications:\n"
            f"{original_intent}\n\n"
            "The proposed dry-run plan:\n"
            f"{plan}\n\n"
            "Does the plan match Mario's actual intent and scope? If it drifts "
            "(touches systems he didn't ask about, misunderstands the goal, or "
            "over-/under-reaches), set approved=false and give specific, "
            "actionable corrections. If it's aligned, set approved=true and "
            "leave corrections empty."
        )
        resp = self.ask(
            prompt,
            thinking=False,  # gated per-model anyway
            output_config={"format": {"type": "json_schema",
                                      "schema": _VERDICT_SCHEMA}},
            system=ORCHESTRATE_SYSTEM,
        )
        data = self._parse_json(resp.text)
        return ValidationVerdict(
            approved=bool(data.get("approved", False)),
            corrections=str(data.get("corrections", "")).strip(),
        )

    def classify_complexity(self, context: str, plan: str) -> tuple[str, str, str]:
        """Judge a task on two INDEPENDENT axes.

        complexity -> which model tier should implement:
          low (trivial mechanical edit), medium (moderate work), high (complex).
        effort -> how much reasoning depth, independent of complexity:
          low (localized/mechanical), medium, high (deep multi-step). A hard bug
          confined to one function can be high complexity but low/medium effort.

        Returns (complexity, effort, reason). Conservative: when uncertain it
        picks the HIGHER level, and a malformed response defaults to high/high.
        """
        prompt = (
            "Assess an implementation task on two INDEPENDENT axes.\n\n"
            "complexity (which model is needed):\n"
            "- low: trivial mechanical edit (typo, comment, config value, rename).\n"
            "- medium: moderate work (small feature, refactor, simple bug fix).\n"
            "- high: complex work (non-trivial logic, new subsystem, tricky bug).\n\n"
            "effort (how much reasoning depth), independent of complexity:\n"
            "- low: localized/mechanical, little reasoning.\n"
            "- medium: some reasoning across a few places.\n"
            "- high: deep, careful multi-step reasoning.\n"
            "A hard bug confined to one function can be high complexity but "
            "low/medium effort; a trivial change across many files can be higher "
            "effort.\n\n"
            "When uncertain, pick the HIGHER level on either axis.\n\n"
            f"Task context:\n{context}\n\nPlan:\n{plan}\n\n"
            "Give complexity, effort, and a one-line reason."
        )
        resp = self.ask(
            prompt,
            thinking=False,
            output_config={"format": {"type": "json_schema",
                                      "schema": _COMPLEXITY_SCHEMA}},
            system=ORCHESTRATE_SYSTEM,
        )
        data = self._parse_json(resp.text)
        complexity = str(data.get("complexity", "high")).lower().strip()
        if complexity not in ("low", "medium", "high"):
            complexity = "high"
        effort = str(data.get("effort", "high")).lower().strip()
        if effort not in ("low", "medium", "high"):
            effort = "high"
        return complexity, effort, str(data.get("reason", "")).strip()

    def clarify(self, *, clarify_instructions: str,
                messages: list[dict]) -> ClarifyTurn:
        """One clarification turn over the running transcript."""
        resp = self.client.complete(
            model=self.model,
            system=ORCHESTRATE_SYSTEM + "\n\n" + clarify_instructions,
            messages=messages,
            effort=self.effort,
            thinking=False,
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
            task_type=str(data.get("task_type", "")),
        )

    # ------------------------------------------------------------------ #
    # Implementation skill (task prompt: IMPLEMENT_SYSTEM)
    # ------------------------------------------------------------------ #
    def implement_with_tools(self, instructions: str, *, repo_path: str,
                             executor, session_id: Optional[str] = None,
                             controller=None, max_steps: int = 40,
                             max_tokens: int = 16000,
                             effort: Optional[str] = None) -> ImplementResult:
        """Run the bash tool-use loop until the implementer finishes or is
        cancelled. Uses IMPLEMENT_SYSTEM regardless of which model runs it.
        `effort` overrides the model's configured effort (the independent
        effort filter); None keeps the model default. Gated per-model by the
        client, so it's safe on any model."""
        from ..execution.executor import CommandStopped

        effort = effort or self.effort

        messages: list[dict] = [{"role": "user", "content": (
            "Implement the following instructions in the repository. Follow the "
            "project's conventions exactly. Commit locally when done; do not "
            "push.\n\n" + instructions)}]
        commands: list[str] = []
        final_text = ""
        last_sha = None
        step = 0

        for step in range(1, max_steps + 1):
            if controller and session_id and (
                    controller.should_stop(session_id)
                    or controller.should_pause(session_id)):
                return ImplementResult(text=final_text, commands=commands,
                                       steps=step - 1, stopped=True,
                                       last_commit_sha=last_sha)

            resp = self.client.complete(
                model=self.model, system=IMPLEMENT_SYSTEM, messages=messages,
                max_tokens=max_tokens, effort=effort, thinking=True,
                tools=[_BASH_TOOL], agent_label=self.label)
            msg = resp.raw

            if getattr(msg, "stop_reason", None) != "tool_use":
                final_text = resp.text
                break

            messages.append({"role": "assistant", "content": msg.content})
            results = []
            for block in msg.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                if block.name == "run_bash":
                    cmd = block.input.get("command", "")
                    commands.append(cmd)
                    try:
                        r = executor.run(cmd, cwd=repo_path, session_id=session_id)
                    except CommandStopped:
                        return ImplementResult(text=final_text, commands=commands,
                                               steps=step, stopped=True,
                                               last_commit_sha=last_sha)
                    if "git commit" in cmd and r.ok:
                        sha = executor.run("git rev-parse HEAD", cwd=repo_path,
                                           session_id=session_id)
                        if sha.ok:
                            last_sha = sha.stdout.strip()
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": (f"exit={r.returncode}\n"
                                    f"stdout:\n{r.stdout[:6000]}\n"
                                    f"stderr:\n{r.stderr[:2000]}"),
                        "is_error": not r.ok,
                    })
            messages.append({"role": "user", "content": results})
        else:
            logger.warning("Implementer hit max_steps (%d) for session %s",
                           max_steps, session_id)

        return ImplementResult(text=final_text, commands=commands, steps=step,
                               last_commit_sha=last_sha)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_json(text: str) -> Any:
        """Parse a JSON object from a response, tolerating code fences."""
        cleaned = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1).strip()
        return json.loads(cleaned)
