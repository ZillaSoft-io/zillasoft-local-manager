"""Jira integration — fetch a ticket for Haiku's context (spec §6.5).

Uses Basic auth (email + API token) against the Jira Cloud REST v3 API.
Description is Atlassian Document Format (ADF); we flatten it to plain text.
`configured()` gates the call until JIRA_API_TOKEN is set.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Issue key like BUG-123, SUPPORT-42.
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


class JiraError(Exception):
    pass


class JiraClient:
    def __init__(self, config, http_client: Optional[httpx.Client] = None):
        self._config = config
        self._http = http_client

    def configured(self) -> bool:
        return self._config.is_set(self._config.get_raw("JIRA_API_TOKEN"))

    @staticmethod
    def extract_key(ref: str) -> Optional[str]:
        m = _KEY_RE.search(ref or "")
        return m.group(1) if m else None

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        host = self._config.require("JIRA_HOST").rstrip("/")
        email = self._config.require("JIRA_EMAIL")
        token = self._config.require("JIRA_API_TOKEN")
        return httpx.Client(base_url=host, auth=(email, token), timeout=15.0)

    def fetch_issue(self, key: str) -> dict[str, Any]:
        if not self.configured():
            raise JiraError("Jira is not configured (JIRA_API_TOKEN unset).")
        try:
            resp = self._client().get(f"/rest/api/3/issue/{key}")
            resp.raise_for_status()
            issue = resp.json()
        except httpx.HTTPError as exc:
            raise JiraError(f"Jira fetch failed: {exc}") from exc
        return self._summarize(issue)

    @classmethod
    def _summarize(cls, issue: dict[str, Any]) -> dict[str, Any]:
        fields = issue.get("fields", {}) or {}
        priority = fields.get("priority") or {}
        status = fields.get("status") or {}
        assignee = fields.get("assignee") or {}
        return {
            "source": "jira",
            "key": issue.get("key"),
            "title": fields.get("summary"),
            "description": cls._adf_to_text(fields.get("description")),
            "status": status.get("name"),
            "priority": priority.get("name"),
            "assignee": assignee.get("displayName"),
            "attachments": [a.get("filename")
                            for a in fields.get("attachment", []) or []],
        }

    @classmethod
    def _adf_to_text(cls, node: Any) -> str:
        """Flatten Atlassian Document Format (or plain string) to text."""
        if node is None:
            return ""
        if isinstance(node, str):
            return node
        parts: list[str] = []
        if isinstance(node, dict):
            if node.get("type") == "text" and "text" in node:
                parts.append(node["text"])
            for child in node.get("content", []) or []:
                parts.append(cls._adf_to_text(child))
            if node.get("type") in ("paragraph", "heading"):
                parts.append("\n")
        elif isinstance(node, list):
            for child in node:
                parts.append(cls._adf_to_text(child))
        return "".join(parts).strip()
