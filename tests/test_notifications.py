"""Phase 4 — notifications: desktop gating + Brevo email (mocked)."""
from __future__ import annotations

import httpx

from app.notifications import Notifier


# --------------------------- desktop --------------------------- #
def test_desktop_disabled_by_default(config):
    calls = []
    n = Notifier(config, desktop_fn=lambda **k: calls.append(k))
    assert n.desktop("t", "m") is False   # DESKTOP_ENABLED=false in sample env
    assert calls == []


def test_desktop_enabled_calls_fn(config):
    config.set("NOTIFICATIONS_DESKTOP_ENABLED", "true", actor="agent")
    calls = []
    n = Notifier(config, desktop_fn=lambda **k: calls.append(k))
    assert n.desktop("Title", "Body") is True
    assert calls == [{"title": "Title", "message": "Body"}]


# --------------------------- email --------------------------- #
def test_email_skipped_without_key(config):
    # EMAIL_ENABLED=true but BREVO_API_KEY unset in sample env
    n = Notifier(config)
    assert n.email("subj", "<p>hi</p>") is False


def test_email_sends_with_mock(config):
    config.set("BREVO_API_KEY", "brevo-key", actor="system")  # credential
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("api-key")
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(201, json={"messageId": "1"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    n = Notifier(config, http_client=http)
    assert n.email("Deployed", "<p>done</p>", to="m@x.com") is True
    assert captured["url"] == "https://api.brevo.com/v3/smtp/email"
    assert captured["api_key"] == "brevo-key"
    assert captured["body"]["to"] == [{"email": "m@x.com"}]
    assert captured["body"]["subject"] == "Deployed"


# --------------------------- notify dispatch + gating --------------------------- #
def test_notify_email_gated_by_toggle(config):
    config.set("BREVO_API_KEY", "brevo-key", actor="system")
    sent = {"n": 0}

    def handler(request):
        sent["n"] += 1
        return httpx.Response(201, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    n = Notifier(config, desktop_fn=lambda **k: None, http_client=http)

    # ON_SUCCESS not set -> defaults False -> no email
    res = n.notify("success", title="Done", message="ok")
    assert res["email"] is False
    assert sent["n"] == 0

    # enable the toggle -> email fires
    config.set("NOTIFICATIONS_EMAIL_ON_SUCCESS", "true", actor="agent")
    res = n.notify("success", title="Done", message="ok",
                   email_subject="Deployed")
    assert res["email"] is True
    assert sent["n"] == 1
