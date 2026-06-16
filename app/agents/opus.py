"""Opus — code fixer & builder (spec §3.3).

Opus gets filesystem access through a single client-side `run_bash` tool. The
orchestrator runs the tool-use loop: Opus emits bash commands, the CodeExecutor
runs them in the target repo (write files, git commit, etc.), results go back,
until Opus stops. Kill/pause signals are honored between steps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .base import Agent
from .prompts import OPUS_SYSTEM

logger = logging.getLogger(__name__)

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


@dataclass
class OpusResult:
    text: str                       # Opus's final message
    commands: list[str] = field(default_factory=list)
    steps: int = 0
    stopped: bool = False           # cancelled via kill/pause signal
    last_commit_sha: Optional[str] = None


class OpusAgent(Agent):
    label = "opus"
    system_prompt = OPUS_SYSTEM
    model_key = "ANTHROPIC_MODEL_OPUS"
    effort_key = "ANTHROPIC_EFFORT_OPUS"

    def implement_with_tools(self, instructions: str, *, repo_path: str,
                             executor, session_id: Optional[str] = None,
                             controller=None, max_steps: int = 40,
                             max_tokens: int = 16000) -> OpusResult:
        """Run the bash tool-use loop until Opus finishes or is cancelled."""
        from ..execution.executor import CommandStopped

        messages: list[dict] = [{"role": "user", "content": (
            "Implement the following instructions in the repository. Follow the "
            "project's conventions exactly. Commit locally when done; do not "
            "push.\n\n" + instructions)}]
        commands: list[str] = []
        final_text = ""
        last_sha = None

        for step in range(1, max_steps + 1):
            if controller and session_id and (
                    controller.should_stop(session_id)
                    or controller.should_pause(session_id)):
                return OpusResult(text=final_text, commands=commands,
                                  steps=step - 1, stopped=True,
                                  last_commit_sha=last_sha)

            resp = self.client.complete(
                model=self.model, system=self.system_prompt, messages=messages,
                max_tokens=max_tokens, effort=self.effort, thinking=True,
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
                        return OpusResult(text=final_text, commands=commands,
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
            logger.warning("Opus hit max_steps (%d) for session %s",
                           max_steps, session_id)

        return OpusResult(text=final_text, commands=commands, steps=step,
                          last_commit_sha=last_sha)
