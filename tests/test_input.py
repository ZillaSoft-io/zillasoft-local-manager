"""Phase 3 — input handling: conversation flow, external context, attachments, API."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents import build_agents
from app.audit import AuditTrail
from app.config import ConfigHandler
from app.database import Database
from app.input import ConversationManager
from app.integrations import SentryClient
from tests.fakes import FakeMessage, FakeSDK


def _turn(status, message, summary="", scope="", cap=0.0, stack=""):
    return {"status": status, "message": message, "context_summary": summary,
            "scope_level": scope, "monthly_cap": cap, "recommended_stack": stack}


def _clarify_sdk(turns):
    st = {"i": 0}

    def responder(params):
        if "haiku" in params["model"]:
            t = turns[min(st["i"], len(turns) - 1)]
            st["i"] += 1
            return FakeMessage(json.dumps(t))
        return FakeMessage("ok")
    return FakeSDK(responder)


@pytest.fixture
def make_cm(config, tmp_path):
    counter = {"n": 0}

    def make(turns, sentry=None, jira=None):
        counter["n"] += 1
        sdk = _clarify_sdk(turns)
        haiku, *_ = build_agents(config, sdk_client=sdk)
        db = Database(tmp_path / f"cm_{counter['n']}.db")
        audit = AuditTrail(tmp_path / "audit")
        cm = ConversationManager(config, db, audit, haiku, sentry=sentry,
                                 jira=jira, uploads_dir=tmp_path / "uploads")
        return cm, sdk, db
    return make


# --------------------------- conversation flow --------------------------- #
def test_asks_then_ready(make_cm):
    cm, sdk, db = make_cm([
        _turn("asking", "Which project?"),
        _turn("ready", "Got it", summary="Add dark mode to navbar",
              scope="uncapped"),
    ])
    sid = cm.create_session("feature", "website")
    t1 = cm.handle_message(sid, "add dark mode")
    assert t1.status == "asking"
    t2 = cm.handle_message(sid, "website")
    assert t2.status == "ready"
    assert t2.context_summary == "Add dark mode to navbar"

    session = db.get_session(sid)
    assert session["haiku_context"]["summary"] == "Add dark mode to navbar"
    assert session["scope_level"] == "uncapped"
    roles = [m["role"] for m in db.list_messages(sid)]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_capped_scope_sets_global_cost_cap(make_cm):
    cm, sdk, db = make_cm([_turn("ready", "ok", summary="fix", scope="capped",
                                 cap=150.0)])
    sid = cm.create_session("bug_fix", "snipzilla")
    cm.handle_message(sid, "fix the crash, capped at $150")
    assert cm.config.get("LOCAL_MANAGER_MONTHLY_COST_CAP") == 150.0


# --------------------------- external context injection --------------------------- #
def test_sentry_reference_is_fetched_and_injected(make_cm, config):
    def handler(request):
        return httpx.Response(200, json={
            "eventID": "a" * 32,
            "metadata": {"type": "AttributeError", "value": "foo is None"},
            "culprit": "auth.py",
        })
    http = httpx.Client(base_url="https://sentry.io/api/0",
                        transport=httpx.MockTransport(handler))
    sentry = SentryClient(config, http_client=http)
    cm, sdk, db = make_cm([_turn("ready", "ok", summary="s", scope="uncapped")],
                          sentry=sentry)
    sid = cm.create_session("bug_fix", "website")
    cm.handle_message(sid, "crash here: https://sentry.io/x/events/" + "a" * 32 + "/")

    sysmsgs = [m for m in db.list_messages(sid) if m["role"] == "system"]
    assert any("EXTERNAL CONTEXT" in m["content"] for m in sysmsgs)
    assert db.get_session(sid)["input_source"] == "sentry"
    # The fetched context reaches Haiku via the system-prompt addendum.
    haiku_calls = [c for c in sdk.calls if "haiku" in c["model"]]
    assert any("EXTERNAL CONTEXT" in c["system"] for c in haiku_calls)
    # System (external) messages are NOT sent as message turns.
    for c in haiku_calls:
        assert all(m["role"] != "system" for m in c["messages"])


# --------------------------- attachments (vision) --------------------------- #
def test_attachment_becomes_image_block(make_cm):
    cm, sdk, db = make_cm([_turn("asking", "anything else?")])
    sid = cm.create_session("bug_fix", "website")
    ref = cm.attachments.save(sid, "shot.png", b"\x89PNG fake-bytes")
    assert ref["is_image"] is True
    cm.handle_message(sid, "see this screenshot", attachment_refs=[ref])

    haiku_call = [c for c in sdk.calls if "haiku" in c["model"]][0]
    user_msgs = [m for m in haiku_call["messages"] if m["role"] == "user"]
    content = user_msgs[-1]["content"]
    assert isinstance(content, list)
    assert any(b.get("type") == "image" for b in content)
    assert any(b.get("type") == "text" for b in content)


# --------------------------- API endpoints --------------------------- #
async def _noop_lifespan(app):
    yield


@pytest.fixture
def input_api(env_file, tmp_path):
    import app.main as main
    config = ConfigHandler(env_path=env_file)
    sdk = _clarify_sdk([_turn("ready", "Got it", summary="summary",
                              scope="uncapped")])
    haiku, *_ = build_agents(config, sdk_client=sdk)
    main.state.config = config
    main.state.db = Database(tmp_path / "api.db")
    main.state.audit = AuditTrail(tmp_path / "audit")
    main.state.auth_token = config.ensure_auth_token()
    main.state.conversation = ConversationManager(
        config, main.state.db, main.state.audit, haiku,
        uploads_dir=tmp_path / "uploads")
    main.app.router.lifespan_context = _noop_lifespan
    return TestClient(main.app), main.state.auth_token


def test_api_requires_auth(input_api):
    c, _ = input_api
    r = c.post("/api/input/sessions", json={"task_type": "feature"})
    assert r.status_code == 401


def test_api_create_session_with_message(input_api):
    c, token = input_api
    h = {"Authorization": f"Bearer {token}"}
    r = c.post("/api/input/sessions",
               json={"task_type": "feature", "project": "website",
                     "message": "add dark mode"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "session_id" in body
    assert body["turn"]["status"] == "ready"

    # transcript persisted
    sid = body["session_id"]
    r2 = c.get(f"/api/input/sessions/{sid}/messages", headers=h)
    assert r2.status_code == 200
    assert len(r2.json()["messages"]) == 2


def test_api_invalid_task_type(input_api):
    c, token = input_api
    r = c.post("/api/input/sessions", json={"task_type": "nonsense"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_api_message_to_unknown_session(input_api):
    c, token = input_api
    r = c.post("/api/input/sessions/nope/messages", json={"message": "hi"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_api_attachment_upload(input_api):
    c, token = input_api
    h = {"Authorization": f"Bearer {token}"}
    sid = c.post("/api/input/sessions", json={"task_type": "bug_fix",
                 "project": "website"}, headers=h).json()["session_id"]
    r = c.post(f"/api/input/sessions/{sid}/attachments",
               files={"file": ("shot.png", b"\x89PNG bytes", "image/png")},
               headers=h)
    assert r.status_code == 200
    assert r.json()["attachment"]["is_image"] is True
