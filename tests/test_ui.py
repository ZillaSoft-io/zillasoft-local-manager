"""Phase 9 — UI serving + config write endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigHandler


async def _noop_lifespan(app):
    yield


@pytest.fixture
def ui(env_file):
    import app.main as main
    config = ConfigHandler(env_path=env_file)
    main.state.config = config
    main.state.auth_token = config.ensure_auth_token()
    main.app.router.lifespan_context = _noop_lifespan
    return TestClient(main.app), config.ensure_auth_token(), config


def test_index_served_public(ui):
    c, _, _ = ui
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Local Manager" in r.text


def test_config_set_requires_auth(ui):
    c, _, _ = ui
    assert c.post("/api/config/set",
                  json={"key": "LOCAL_MANAGER_LOG_LEVEL", "value": "DEBUG"}
                  ).status_code == 401


def test_config_set_non_credential(ui):
    c, token, config = ui
    r = c.post("/api/config/set",
               json={"key": "LOCAL_MANAGER_LOG_LEVEL", "value": "DEBUG"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["value"] == "DEBUG"
    assert config.get_raw("LOCAL_MANAGER_LOG_LEVEL") == "DEBUG"


def test_config_set_credential_allowed_via_ui(ui):
    c, token, config = ui
    r = c.post("/api/config/set",
               json={"key": "GITHUB_TOKEN", "value": "ghp_newtoken"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_credential"] is True
    assert body["value"] == "<set>"            # masked in response
    assert config.get_raw("GITHUB_TOKEN") == "ghp_newtoken"  # actually written


def test_config_set_validation_error(ui):
    c, token, _ = ui
    r = c.post("/api/config/set",
               json={"key": "LOCAL_MANAGER_PORT", "value": "not-a-number"},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400
