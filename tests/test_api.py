"""Phase 1 — API tests: health (public), auth gate, status/config/sessions."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.audit import AuditTrail
from app.config import ConfigHandler
from app.database import Database


@pytest.fixture
def client(env_file, tmp_path, monkeypatch):
    """Build a TestClient with state wired to a temp env/db/audit.

    Bypasses the real lifespan so tests don't touch the project .env.
    """
    config = ConfigHandler(env_path=env_file)
    main.state.config = config
    main.state.db = Database(tmp_path / "api.db")
    main.state.audit = AuditTrail(tmp_path / "audit")
    main.state.auth_token = config.ensure_auth_token()
    # Disable lifespan (state already wired manually).
    main.app.router.lifespan_context = _noop_lifespan
    return TestClient(main.app), main.state.auth_token


async def _noop_lifespan(app):
    yield


def test_health_is_public(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_requires_auth(client):
    c, _ = client
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/config").status_code == 401


def test_api_rejects_bad_token(client):
    c, _ = client
    r = c.get("/api/status", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_api_status_with_token(client):
    c, token = client
    r = c.get("/api/status", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "zillasoft-local-manager"
    assert body["auto_deploy"] is False


def test_api_config_masks_credentials(client):
    c, token = client
    r = c.get("/api/config", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["GITHUB_TOKEN"] == "<set>"
    assert body["ANTHROPIC_API_KEY"] == "<unset>"


def test_api_sessions_roundtrip(client):
    c, token = client
    h = {"Authorization": f"Bearer {token}"}
    sid = main.state.db.create_session(task_type="bug_fix", project="website")
    r = c.get("/api/sessions", headers=h)
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())
    r2 = c.get(f"/api/sessions/{sid}", headers=h)
    assert r2.status_code == 200
    assert r2.json()["project"] == "website"
    assert c.get("/api/sessions/nope", headers=h).status_code == 404
