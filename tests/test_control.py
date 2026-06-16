"""Phase 4 — control: kill switch, pause/resume, expiry, and the API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditTrail
from app.config import ConfigHandler
from app.control import SessionController
from app.cost import MonthlyBudget
from app.database import Database
from app.notifications import Notifier


@pytest.fixture
def controller(config, tmp_path):
    db = Database(tmp_path / "ctl.db")
    audit = AuditTrail(tmp_path / "audit")
    ctrl = SessionController(config, db, audit,
                            pause_dir=tmp_path / "paused", notifier=None)
    return ctrl, db


def _age_pause_file(ctrl, session_id, days):
    path = ctrl._pause_path(session_id)
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["paused_at"] = (datetime.now(timezone.utc)
                        - timedelta(days=days)).isoformat()
    path.write_text(json.dumps(rec), encoding="utf-8")


# --------------------------- kill --------------------------- #
def test_kill_marks_stopped_and_sets_signal(controller):
    ctrl, db = controller
    sid = db.create_session(task_type="bug_fix", project="website")
    ctrl.kill(sid, reason="user stop")
    s = db.get_session(sid)
    assert s["status"] == "stopped"
    assert s["error_message"] == "user stop"
    assert ctrl.should_stop(sid) is True


def test_kill_unknown_raises(controller):
    ctrl, _ = controller
    with pytest.raises(KeyError):
        ctrl.kill("nope")


# --------------------------- pause / resume --------------------------- #
def test_pause_then_resume(controller):
    ctrl, db = controller
    sid = db.create_session(task_type="feature", project="snipzilla")
    path = ctrl.save_pause(sid, {"cycle": 2, "instructions": "edit x"})
    assert path.exists()
    assert db.get_session(sid)["status"] == "paused"
    assert ctrl.should_pause(sid) is True

    record = ctrl.resume(sid)
    assert record["snapshot"]["cycle"] == 2
    assert db.get_session(sid)["status"] == "in_progress"
    assert ctrl.should_pause(sid) is False   # signal cleared on resume


def test_list_resumable(controller):
    ctrl, db = controller
    a = db.create_session(task_type="bug_fix", project="website")
    b = db.create_session(task_type="feature", project="snipzilla")
    ctrl.save_pause(a, {"cycle": 1})
    ctrl.save_pause(b, {"cycle": 1})
    ids = {r["session_id"] for r in ctrl.list_resumable()}
    assert ids == {a, b}


def test_resume_expired_raises_and_fails_session(controller):
    ctrl, db = controller
    sid = db.create_session(task_type="bug_fix", project="website")
    ctrl.save_pause(sid, {"cycle": 1})
    _age_pause_file(ctrl, sid, days=10)   # > 7-day expiry
    with pytest.raises(KeyError):
        ctrl.resume(sid)
    assert db.get_session(sid)["status"] == "failed"


def test_sweep_expired(controller):
    ctrl, db = controller
    fresh = db.create_session(task_type="bug_fix", project="website")
    stale = db.create_session(task_type="feature", project="snipzilla")
    ctrl.save_pause(fresh, {"cycle": 1})
    ctrl.save_pause(stale, {"cycle": 1})
    _age_pause_file(ctrl, stale, days=30)
    assert ctrl.sweep_expired() == 1
    assert db.get_session(stale)["status"] == "failed"
    assert not ctrl._pause_path(stale).exists()
    assert ctrl._pause_path(fresh).exists()   # fresh untouched


# --------------------------- API --------------------------- #
async def _noop_lifespan(app):
    yield


@pytest.fixture
def control_api(env_file, tmp_path):
    import app.main as main
    config = ConfigHandler(env_path=env_file)
    main.state.config = config
    main.state.db = Database(tmp_path / "api.db")
    main.state.audit = AuditTrail(tmp_path / "audit")
    main.state.auth_token = config.ensure_auth_token()
    main.state.budget = MonthlyBudget(config)
    main.state.notifier = Notifier(config, desktop_fn=lambda **k: None)
    main.state.controller = SessionController(
        config, main.state.db, main.state.audit,
        pause_dir=tmp_path / "paused", notifier=main.state.notifier)
    main.app.router.lifespan_context = _noop_lifespan
    return TestClient(main.app), main.state.auth_token, main.state.db


def test_api_kill_pause_resume_cost(control_api):
    c, token, db = control_api
    h = {"Authorization": f"Bearer {token}"}
    sid = db.create_session(task_type="bug_fix", project="website")

    # kill
    r = c.post(f"/api/sessions/{sid}/kill", json={"reason": "stop"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "stopped"

    # pause + list + resume
    sid2 = db.create_session(task_type="feature", project="snipzilla")
    r = c.post(f"/api/sessions/{sid2}/pause",
               json={"snapshot": {"cycle": 1}}, headers=h)
    assert r.status_code == 200
    assert db.get_session(sid2)["status"] == "paused"
    listed = c.get("/api/paused", headers=h).json()["paused"]
    assert any(p["session_id"] == sid2 for p in listed)
    r = c.post(f"/api/sessions/{sid2}/resume", headers=h)
    assert r.status_code == 200
    assert r.json()["record"]["snapshot"]["cycle"] == 1

    # cost
    assert c.get("/api/cost", headers=h).json()["cap"] == 100.0
    sc = c.get(f"/api/sessions/{sid}/cost", headers=h)
    assert sc.status_code == 200


def test_api_control_requires_auth(control_api):
    c, _, db = control_api
    sid = db.create_session(task_type="bug_fix")
    assert c.post(f"/api/sessions/{sid}/kill", json={}).status_code == 401
    assert c.get("/api/cost").status_code == 401


def test_api_kill_unknown_404(control_api):
    c, token, _ = control_api
    r = c.post("/api/sessions/nope/kill", json={},
               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
