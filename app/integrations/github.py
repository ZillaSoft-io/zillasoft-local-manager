"""GitHub integration — create repos under the ZillaSoft org (spec §9.2).

Repo creation is a Mario-authorized action (it's an outward side effect), so
the provisioner only calls it on an explicit request, not automatically.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


class GitHubError(Exception):
    pass


class GitHubClient:
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
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def create_repo(self, name: str, *, private: bool = True,
                    description: str = "") -> dict:
        owner = self._config.get_raw("GITHUB_OWNER", "")
        if not owner:
            raise GitHubError("GITHUB_OWNER not configured.")
        try:
            resp = self._client().post(f"/orgs/{owner}/repos", json={
                "name": name, "private": private, "description": description})
            if resp.status_code == 422:
                raise GitHubError(f"Repo '{owner}/{name}' already exists or "
                                  f"name is invalid.")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub repo creation failed: {exc}") from exc
        return {
            "slug": data.get("full_name", f"{owner}/{name}"),
            "html_url": data.get("html_url"),
            "clone_url": data.get("clone_url"),
            "default_branch": data.get("default_branch", "main"),
        }
