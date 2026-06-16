"""ReleaseManager — approve/reject/rollback (spec §8.1, Phase 6 git ops).

Approve finalizes any leftover changes, pushes to the project's branch, and
marks the session approved. Reject discards the session's local commits (reset
to the base SHA captured at run start). Rollback reverts the pushed commit and
pushes the revert. Deployment *monitoring* after push is Phase 8.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .errors import ConfigValidationError
from .vcs import GitOps, generate_commit_message

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReleaseError(Exception):
    pass


class ReleaseManager:
    def __init__(self, config, db, audit, executor, notifier, haiku=None):
        self._config = config
        self._db = db
        self._audit = audit
        self._executor = executor
        self._notifier = notifier
        self._haiku = haiku

    # ------------------------------------------------------------------ #
    def _repo_cfg(self, project: str) -> tuple[GitOps, str, str, str]:
        up = project.upper()
        repo = self._config.get_raw(f"PROJECT_{up}_REPO_PATH")
        branch = self._config.get_raw(f"PROJECT_{up}_GITHUB_BRANCH", "main")
        slug = self._config.get_raw(f"PROJECT_{up}_GITHUB_REPO", "")
        if not repo:
            raise ReleaseError(f"No repo path configured for project {project}.")
        return GitOps(repo, self._executor), repo, branch, slug

    def _push_url(self, slug: str) -> str:
        token = self._config.get_raw("GITHUB_TOKEN")
        if not self._config.is_set(token):
            raise ConfigValidationError(
                "GITHUB_TOKEN is not set — set it via the UI to push.")
        return f"https://x-access-token:{token}@github.com/{slug}.git"

    def _deployment(self, session: dict) -> dict:
        dep = session.get("deployment_status")
        return dict(dep) if isinstance(dep, dict) else {}

    # ------------------------------------------------------------------ #
    def approve(self, session_id: str, *, notes: str = "",
                push_url: Optional[str] = None) -> dict:
        session = self._require(session_id)
        project = session.get("project")
        if not project:
            raise ReleaseError("Cannot push a new-app session (no project repo).")
        git, repo, branch, slug = self._repo_cfg(project)

        # Finalize any uncommitted changes with an auto-generated message.
        if not git.is_clean():
            msg = generate_commit_message(
                git.diff_stat("HEAD"),
                task_summary=(session.get("haiku_context") or {}).get("summary", ""),
                haiku=self._haiku)
            git.commit(msg)
        sha = git.head_sha()

        url = push_url or self._push_url(slug)
        git.push(branch, url=url)

        dep = self._deployment(session)
        dep["github_commit"] = {"sha": sha, "branch": branch,
                                "pushed_at": _now()}
        self._db.update_session(session_id, status="approved",
                                mario_approved=True, mario_approved_at=_now(),
                                deployment_status=dep)
        self._audit.update(session_id, project,
                           {"mario_review": {"approved": True, "notes": notes,
                                             "approved_at": _now()},
                            "deployment": {"github_commit": dep["github_commit"]}})
        self._notifier.notify(
            "success", title="Pushed",
            message=f"Session {session_id[:8]} approved and pushed to "
                    f"{slug}@{branch}.",
            email_subject="ZillaSoft Local Manager — change pushed")
        logger.info("Session %s approved + pushed (%s).", session_id, sha)
        return {"status": "approved", "sha": sha, "branch": branch}

    def reject(self, session_id: str, *, notes: str = "") -> dict:
        session = self._require(session_id)
        project = session.get("project")
        base = self._deployment(session).get("base_sha")
        if project and base:
            git, *_ = self._repo_cfg(project)
            git.reset_hard(base)   # discard the session's local commits
        self._db.update_session(session_id, status="failed",
                                error_message=f"rejected by Mario: {notes}".strip())
        self._audit.update(session_id, project,
                           {"mario_review": {"approved": False, "notes": notes}})
        logger.info("Session %s rejected.", session_id)
        return {"status": "rejected"}

    def rollback(self, session_id: str, *, push_url: Optional[str] = None) -> dict:
        session = self._require(session_id)
        project = session.get("project")
        if not project:
            raise ReleaseError("Cannot rollback a new-app session.")
        git, repo, branch, slug = self._repo_cfg(project)
        dep = self._deployment(session)
        sha = (dep.get("github_commit") or {}).get("sha") or git.head_sha()
        revert_sha = git.revert(sha)
        url = push_url or self._push_url(slug)
        git.push(branch, url=url)
        dep["rollback"] = {"reverted_sha": sha, "revert_commit": revert_sha,
                           "at": _now()}
        self._db.update_session(session_id, status="rolled_back",
                                deployment_status=dep)
        self._audit.update(session_id, project, {"rollback": dep["rollback"]})
        self._notifier.notify(
            "failure", title="Rolled back",
            message=f"Session {session_id[:8]} rolled back ({sha[:8]}).",
            email_subject="ZillaSoft Local Manager — rollback")
        logger.info("Session %s rolled back (reverted %s).", session_id, sha)
        return {"status": "rolled_back", "reverted_sha": sha,
                "revert_commit": revert_sha}

    # ------------------------------------------------------------------ #
    def _require(self, session_id: str) -> dict:
        session = self._db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")
        return session
