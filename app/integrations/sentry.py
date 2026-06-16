"""Sentry integration — fetch a crash report for Haiku's context (spec §6.4).

Credentials live in .env (SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT_*).
Until those are set, `configured()` returns False and the conversation manager
skips the fetch gracefully. Event extraction is defensive — Sentry's event JSON
varies by SDK/platform, so we pull what's reliably present and keep the raw.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://sentry.io/api/0"
# 32-hex event id, or a Sentry URL containing /events/<id>/ or /issues/<id>/.
_EVENT_IN_URL = re.compile(r"/(?:events|issues)/([0-9a-fA-F]{32}|\d+)/?")
_BARE_EVENT_ID = re.compile(r"^[0-9a-fA-F]{32}$")


class SentryError(Exception):
    pass


class SentryClient:
    def __init__(self, config, http_client: Optional[httpx.Client] = None):
        self._config = config
        self._http = http_client

    def configured(self) -> bool:
        return self._config.is_set(self._config.get_raw("SENTRY_AUTH_TOKEN"))

    @staticmethod
    def extract_event_id(ref: str) -> Optional[str]:
        ref = (ref or "").strip()
        m = _EVENT_IN_URL.search(ref)
        if m:
            return m.group(1)
        if _BARE_EVENT_ID.match(ref):
            return ref
        return None

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        token = self._config.require("SENTRY_AUTH_TOKEN")
        return httpx.Client(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )

    def fetch_event(self, event_id: str,
                    project: Optional[str] = None) -> dict[str, Any]:
        """Fetch and summarize a Sentry event. `project` defaults to the
        website project slug; callers pass the right one per target project."""
        if not self.configured():
            raise SentryError("Sentry is not configured (SENTRY_AUTH_TOKEN unset).")
        org = self._config.get_raw("SENTRY_ORG", "")
        project = project or self._config.get_raw("SENTRY_PROJECT_WEBSITE", "")
        path = f"/projects/{org}/{project}/events/{event_id}/"
        try:
            resp = self._client().get(path)
            resp.raise_for_status()
            event = resp.json()
        except httpx.HTTPError as exc:
            raise SentryError(f"Sentry fetch failed: {exc}") from exc
        return self._summarize(event)

    @staticmethod
    def _summarize(event: dict[str, Any]) -> dict[str, Any]:
        meta = event.get("metadata", {}) or {}
        # Stacktrace lives under entries[type=exception]; pull defensively.
        stacktrace = None
        for entry in event.get("entries", []) or []:
            if entry.get("type") == "exception":
                values = (entry.get("data", {}) or {}).get("values", [])
                if values:
                    stacktrace = values[0].get("stacktrace")
                break
        return {
            "source": "sentry",
            "event_id": event.get("eventID") or event.get("id"),
            "title": event.get("title") or meta.get("title"),
            "crash_type": meta.get("type"),
            "message": meta.get("value") or event.get("message"),
            "culprit": event.get("culprit"),
            "environment": event.get("environment"),
            "release": (event.get("release") or {}).get("version")
            if isinstance(event.get("release"), dict) else event.get("release"),
            "stacktrace": stacktrace,
        }
