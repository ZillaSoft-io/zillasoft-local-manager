"""GitHub Actions polling (spec §8.2)."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


class GitHubActionsClient:
    def __init__(self, config, http_client: Optional[httpx.Client] = None):
        self._config = config
        self._http = http_client

    def configured(self) -> bool:
        return self._config.is_set(self._config.get_raw("GITHUB_TOKEN"))

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        token = self._config.require("GITHUB_TOKEN")
        return httpx.Client(base_url=_BASE, timeout=20.0, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})

    def latest_run(self, repo: str, workflow: str,
                   branch: Optional[str] = None) -> Optional[dict]:
        params = {"per_page": 1}
        if branch:
            params["branch"] = branch
        resp = self._client().get(
            f"/repos/{repo}/actions/workflows/{workflow}/runs", params=params)
        resp.raise_for_status()
        runs = resp.json().get("workflow_runs", [])
        if not runs:
            return None
        r = runs[0]
        return {"run_id": r.get("id"), "status": r.get("status"),
                "conclusion": r.get("conclusion"), "html_url": r.get("html_url")}

    def poll(self, repo: str, workflow: str, branch: Optional[str] = None, *,
             attempts: int = 30, delay: int = 10,
             sleep: Callable[[float], None] = time.sleep) -> dict:
        """Poll until the latest run completes (or attempts run out)."""
        last = {"status": "unknown", "conclusion": None}
        for i in range(attempts):
            run = self.latest_run(repo, workflow, branch)
            if run:
                last = run
                if run["status"] == "completed":
                    return {"target": "github_actions",
                            "ok": run["conclusion"] == "success", **run}
            if i < attempts - 1:
                sleep(delay)
        return {"target": "github_actions", "ok": False, "timed_out": True, **last}
