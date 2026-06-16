"""Pre-flight checks (spec §8.0).

Startup checks run once on launch; per-session checks run before Sonnet writes
instructions. Results are returned structured so the orchestrator can log them
to the audit trail and warn Mario about dirty trees / pre-existing failures.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from typing import Optional

from .executor import CodeExecutor
from .tests import run_tests

logger = logging.getLogger(__name__)

_MIN_FREE_BYTES = 500 * 1024 * 1024  # 500 MB headroom for build artifacts

_CREDENTIAL_KEYS = (
    "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "SENTRY_AUTH_TOKEN",
    "JIRA_API_TOKEN", "RAILWAY_API_TOKEN", "AWS_ACCESS_KEY_ID",
)


@dataclass
class PreflightResult:
    ok: bool
    checks: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": self.checks, "warnings": self.warnings}


class PreFlight:
    def __init__(self, config, executor: CodeExecutor):
        self._config = config
        self._executor = executor

    # ------------------------------------------------------------------ #
    def startup(self) -> dict:
        """Once-per-launch checks: credentials present, cost cap loaded."""
        creds = {k: self._config.is_set(self._config.get_raw(k))
                 for k in _CREDENTIAL_KEYS}
        return {
            "credentials_present": creds,
            "monthly_cost_cap": self._config.get("LOCAL_MANAGER_MONTHLY_COST_CAP"),
            "current_month_spent": self._config.get(
                "LOCAL_MANAGER_CURRENT_MONTH_SPENT"),
        }

    # ------------------------------------------------------------------ #
    def session(self, *, repo_path: Optional[str], test_command: str,
                session_id: Optional[str] = None,
                run_existing_tests: bool = True) -> PreflightResult:
        checks: dict = {}
        warnings: list[str] = []

        # Git working tree clean? (pre-flight is short and not cancellable —
        # only Opus's long bash loop honors the kill/pause signal.)
        if repo_path:
            status = self._executor.run("git status --porcelain", cwd=repo_path)
            dirty = [l for l in status.stdout.splitlines() if l.strip()]
            checks["git_clean"] = (len(dirty) == 0)
            checks["git_dirty_files"] = dirty[:20]
            if dirty:
                warnings.append(
                    f"{len(dirty)} uncommitted change(s) in the target repo.")

            # Disk space
            free = shutil.disk_usage(repo_path).free
            checks["disk_free_mb"] = free // (1024 * 1024)
            checks["disk_ok"] = free >= _MIN_FREE_BYTES
            if free < _MIN_FREE_BYTES:
                warnings.append("Low disk space (<500 MB free).")

            # Tests currently passing (catch pre-existing failures)
            if run_existing_tests and test_command:
                tr = run_tests(self._executor, repo_path, test_command)
                checks["tests_passing_before"] = tr.ok
                checks["tests_before_summary"] = tr.summary
                if not tr.ok:
                    warnings.append(
                        f"Tests already failing before this session: {tr.summary}. "
                        "New failures may not be caused by this change.")

        # Model availability — can only truly ping with a key.
        checks["model_key_present"] = self._config.is_set(
            self._config.get_raw("ANTHROPIC_API_KEY"))
        if not checks["model_key_present"]:
            warnings.append("ANTHROPIC_API_KEY not set — agents cannot run.")

        # ok = no blocking issues (warnings are advisory, not fatal)
        ok = checks.get("disk_ok", True) and checks["model_key_present"]
        return PreflightResult(ok=ok, checks=checks, warnings=warnings)
