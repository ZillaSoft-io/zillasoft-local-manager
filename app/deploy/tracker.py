"""DeploymentTracker — monitor a push to completion + health (spec §8.2).

Targets are chosen by the project's deploy trigger:
  * railway projects (Snipzilla/Stashzilla): GitHub Actions build + Railway
  * github_actions_manual (website): GitHub Actions + S3 + CloudFront invalidate
All projects finish with a health check. On failure, suggests a rollback.

Each target call is wrapped so one missing credential degrades to a structured
failure instead of crashing the (background) deploy thread.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from .aws import AwsDeploy
from .github_actions import GitHubActionsClient
from .health import HealthChecker
from .railway import RailwayClient

logger = logging.getLogger(__name__)


def _safe(fn, target: str) -> dict:
    try:
        return fn()
    except Exception as exc:  # missing creds / network / API error
        logger.warning("Deploy target %s failed: %s", target, exc)
        return {"target": target, "ok": False, "error": str(exc)}


class DeploymentTracker:
    def __init__(self, config, db, audit, notifier, *, gha=None, railway=None,
                 aws=None, health=None,
                 sleep: Callable[[float], None] = time.sleep):
        self._config = config
        self._db = db
        self._audit = audit
        self._notifier = notifier
        self._gha = gha or GitHubActionsClient(config)
        self._railway = railway or RailwayClient(config)
        self._aws = aws or AwsDeploy(config)
        self._health = health or HealthChecker(config)
        self._sleep = sleep

    def _cfg(self, up: str, key: str, default: str = "") -> str:
        return self._config.get_raw(f"PROJECT_{up}_{key}", default)

    def track(self, session_id: str) -> dict:
        session = self._db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")
        project = session.get("project")
        if not project:
            return {"ok": False, "skipped": "new app — deploy is manual"}

        up = project.upper()
        trigger = self._cfg(up, "DEPLOY_TRIGGER")
        repo = self._cfg(up, "GITHUB_REPO")
        branch = self._cfg(up, "GITHUB_BRANCH", "main")
        workflow = self._cfg(up, "GITHUB_ACTIONS_WORKFLOW")
        results: dict[str, dict] = {}

        if workflow:
            results["github_actions"] = _safe(
                lambda: self._gha.poll(repo, workflow, branch, sleep=self._sleep),
                "github_actions")

        if trigger == "railway":
            pid = self._cfg(up, "RAILWAY_PROJECT_ID")
            svc = self._cfg(up, "RAILWAY_SERVICE_ID")
            results["railway"] = _safe(
                lambda: self._railway.poll(pid, svc, sleep=self._sleep),
                "railway")
        elif trigger in ("github_actions_manual", "github_actions"):
            bucket = self._cfg(up, "S3_BUCKET")
            dist = self._cfg(up, "CLOUDFRONT_DISTRIBUTION")
            if bucket:
                results["s3"] = _safe(
                    lambda: self._aws.verify_s3(bucket), "s3")
            if dist:
                paths = self._cfg(up, "CLOUDFRONT_INVALIDATE_PATHS", "/*")
                results["cloudfront"] = _safe(
                    lambda: self._aws.invalidate(dist, paths.split(",")),
                    "cloudfront")

        results["health_check"] = _safe(
            lambda: self._health.check(project, sleep=self._sleep),
            "health_check")

        ok = all(r.get("ok") for r in results.values())
        dep = dict(session.get("deployment_status") or {})
        dep["targets"] = results
        dep["ok"] = ok
        self._db.update_session(
            session_id, status="deployed" if ok else "failed",
            deployment_status=dep,
            error_message=None if ok else "deployment failed — rollback available")
        self._audit.update(session_id, project,
                           {"deployment_result": {"ok": ok, "targets": results}})

        if ok:
            self._notifier.notify(
                "success", title="Deployment complete",
                message=f"{project} deployed and healthy.",
                email_subject=f"ZillaSoft Local Manager — {project} deployed")
        else:
            self._notifier.notify(
                "failure", title="Deployment failed",
                message=f"{project} deploy failed. Rollback available.",
                email_subject=f"ZillaSoft Local Manager — {project} deploy failed")
        logger.info("Deployment for %s: ok=%s", session_id, ok)
        return {"ok": ok, "targets": results}
