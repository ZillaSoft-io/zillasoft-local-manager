"""Phase 3 — Sentry/Jira integrations: ref extraction + fetch (mocked HTTP)."""
from __future__ import annotations

import httpx

from app.integrations import JiraClient, SentryClient


# --------------------------- reference extraction --------------------------- #
def test_sentry_event_id_extraction():
    eid = "a" * 32
    assert SentryClient.extract_event_id(f"https://sentry.io/x/events/{eid}/") == eid
    assert SentryClient.extract_event_id(eid) == eid
    assert SentryClient.extract_event_id("https://sentry.io/x/issues/4567/") == "4567"
    assert SentryClient.extract_event_id("no event here") is None


def test_jira_key_extraction():
    assert JiraClient.extract_key("see BUG-123 please") == "BUG-123"
    assert JiraClient.extract_key("SUPPORT-9 is open") == "SUPPORT-9"
    assert JiraClient.extract_key("nothing here") is None


# --------------------------- configured() gating --------------------------- #
def test_configured_flags(config):
    assert SentryClient(config).configured() is True   # sample env has token
    assert JiraClient(config).configured() is True


# --------------------------- Sentry fetch (mocked) --------------------------- #
def test_sentry_fetch_summarizes(config):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/projects/zillasoft/zillasoft-website/events/" in request.url.path
        return httpx.Response(200, json={
            "eventID": "a" * 32,
            "title": "AttributeError: foo",
            "metadata": {"type": "AttributeError", "value": "foo is None"},
            "culprit": "server/routers/auth.py",
            "environment": "production",
            "entries": [{"type": "exception", "data": {"values": [
                {"stacktrace": {"frames": [{"function": "signup"}]}}]}}],
        })
    http = httpx.Client(base_url="https://sentry.io/api/0",
                        transport=httpx.MockTransport(handler))
    client = SentryClient(config, http_client=http)
    summary = client.fetch_event("a" * 32, project="zillasoft-website")
    assert summary["crash_type"] == "AttributeError"
    assert summary["message"] == "foo is None"
    assert summary["culprit"] == "server/routers/auth.py"
    assert summary["stacktrace"]["frames"][0]["function"] == "signup"


# --------------------------- Jira fetch (mocked) --------------------------- #
def test_jira_fetch_summarizes_and_flattens_adf(config):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/issue/BUG-123"
        return httpx.Response(200, json={
            "key": "BUG-123",
            "fields": {
                "summary": "Signup 500",
                "description": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": "API returns 500 on signup."}]}]},
                "status": {"name": "Open"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "Mario"},
                "attachment": [{"filename": "error.png"}],
            },
        })
    http = httpx.Client(base_url="https://zillasoft-io.atlassian.net",
                        transport=httpx.MockTransport(handler))
    client = JiraClient(config, http_client=http)
    summary = client.fetch_issue("BUG-123")
    assert summary["title"] == "Signup 500"
    assert "API returns 500 on signup." in summary["description"]
    assert summary["status"] == "Open"
    assert summary["priority"] == "High"
    assert summary["assignee"] == "Mario"
    assert summary["attachments"] == ["error.png"]
