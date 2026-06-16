"""Notifier — desktop + email alerts on key events (spec §11.4).

Desktop notifications fire on session complete/failed/approval/cycle-limit/
kill/pause (Windows, via plyer). Email fires only on success/failure/escalation
(via Brevo) to avoid inbox noise. Both honor the NOTIFICATIONS_* config toggles.

Side effects are best-effort: a failed notification never breaks the pipeline.
`desktop_fn` and `http_client` are injectable for tests.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import httpx

logger = logging.getLogger(__name__)

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"

# Event kinds that send email, and the config toggle that gates each.
_EMAIL_TOGGLE = {
    "success": "NOTIFICATIONS_EMAIL_ON_SUCCESS",
    "failure": "NOTIFICATIONS_EMAIL_ON_FAILURE",
    "escalation": "NOTIFICATIONS_EMAIL_ON_ESCALATION",
}


class Notifier:
    def __init__(self, config,
                 desktop_fn: Optional[Callable[..., None]] = None,
                 http_client: Optional[httpx.Client] = None):
        self._config = config
        self._desktop_fn = desktop_fn
        self._http = http_client

    # ------------------------------------------------------------------ #
    def desktop(self, title: str, message: str) -> bool:
        if not self._config.get("NOTIFICATIONS_DESKTOP_ENABLED", False):
            return False
        try:
            if self._desktop_fn is not None:
                self._desktop_fn(title=title, message=message)
            else:
                from plyer import notification  # lazy: optional at runtime
                notification.notify(title=title, message=message,
                                    app_name="ZillaSoft Local Manager", timeout=10)
            return True
        except Exception as exc:  # best-effort
            logger.warning("Desktop notification failed: %s", exc)
            return False

    def email(self, subject: str, html: str,
              to: Optional[str] = None) -> bool:
        if not self._config.get("NOTIFICATIONS_EMAIL_ENABLED", False):
            return False
        api_key = self._config.get_raw("BREVO_API_KEY")
        if not self._config.is_set(api_key):
            logger.info("Email skipped: BREVO_API_KEY not set.")
            return False
        to = to or self._config.get_raw("NOTIFICATIONS_EMAIL_TO")
        payload = {
            "sender": {
                "email": self._config.get_raw("BREVO_SENDER_EMAIL"),
                "name": "ZillaSoft Local Manager",
            },
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
        }
        try:
            client = self._http or httpx.Client(timeout=15.0)
            resp = client.post(_BREVO_URL, json=payload, headers={
                "api-key": api_key, "accept": "application/json"})
            ok = resp.status_code in (200, 201, 202)
            if not ok:
                logger.warning("Brevo email failed (%s): %s",
                              resp.status_code, resp.text[:200])
            return ok
        except httpx.HTTPError as exc:
            logger.warning("Brevo email error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    def notify(self, kind: str, *, title: str, message: str,
               email_subject: Optional[str] = None,
               email_html: Optional[str] = None) -> dict:
        """Dispatch desktop (always) + email (only for gated kinds)."""
        result = {"desktop": self.desktop(title, message), "email": False}
        toggle = _EMAIL_TOGGLE.get(kind)
        if toggle and self._config.get(toggle, False):
            result["email"] = self.email(
                email_subject or title, email_html or f"<p>{message}</p>")
        return result
