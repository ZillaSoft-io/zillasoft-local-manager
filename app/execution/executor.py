"""CodeExecutor — run shell commands in a repo working directory.

Used by Opus (bash tool) to write/commit code and by Sonnet to run tests.
Commands run through bash when available (Git Bash on Windows) so heredocs and
POSIX syntax work; the cwd is passed as a parameter, so the apostrophe in
"Mario's Docs" never hits the shell.

Cooperative cancellation: if a controller is wired and the session's stop or
pause signal is set, run() raises CommandStopped before launching — the
orchestrator checks between steps, this is the last-line guard.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASH = shutil.which("bash")

# Env var names the agent's shell must NOT see. Bash is intentionally unbounded
# (it can do whatever it needs to fix a bug), but it never needs the app's
# secrets — the push happens later, in our code, behind the approval gate. So we
# strip them from the child environment to limit the blast radius of a
# prompt-injected command (e.g. `echo $ANTHROPIC_API_KEY`).
_SECRET_ENV = re.compile(
    r"(API_KEY|_TOKEN|SECRET|PASSWORD|PASSWD|CONNECTION_STRING|PRIVATE_KEY"
    r"|CREDENTIAL|AUTH|ANTHROPIC|AWS_|JIRA|SENTRY|RAILWAY|STRIPE|BREVO"
    r"|LOCAL_MANAGER_AUTH_TOKEN)", re.IGNORECASE)


def _safe_env() -> dict:
    """A copy of the environment with the app's secrets removed."""
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}


class CommandStopped(Exception):
    """Raised when execution is cancelled via the controller signal."""


@dataclass
class ExecResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class CodeExecutor:
    def __init__(self, controller=None, default_timeout: int = 300):
        self._controller = controller
        self._default_timeout = default_timeout

    def _cancelled(self, session_id: Optional[str]) -> bool:
        if not (self._controller and session_id):
            return False
        return (self._controller.should_stop(session_id)
                or self._controller.should_pause(session_id))

    def run(self, command: str, cwd: "str | Path",
            timeout: Optional[int] = None,
            session_id: Optional[str] = None) -> ExecResult:
        if self._cancelled(session_id):
            raise CommandStopped(f"Execution cancelled before: {command[:60]}")

        cwd = str(cwd)
        Path(cwd).mkdir(parents=True, exist_ok=True)
        argv = ([_BASH, "-lc", command] if _BASH else command)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                argv, cwd=cwd, shell=(_BASH is None),
                capture_output=True, text=True,
                env=_safe_env(),  # secrets stripped — see _SECRET_ENV
                timeout=timeout or self._default_timeout,
            )
            return ExecResult(
                command=command, returncode=proc.returncode,
                stdout=proc.stdout or "", stderr=proc.stderr or "",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                command=command, returncode=-1,
                stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
                stderr=f"Command timed out after {exc.timeout}s",
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=True,
            )
