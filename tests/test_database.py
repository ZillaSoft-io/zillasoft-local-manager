"""Phase 1 — Database tests: session CRUD + JSON column handling."""
from __future__ import annotations

import pytest

from app.database import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "test.db")


def test_create_and_get(db: Database):
    sid = db.create_session(
        task_type="bug_fix", project="stashzilla", scope_level="capped",
        input_source="manual", input_ref="signup 500",
    )
    row = db.get_session(sid)
    assert row is not None
    assert row["project"] == "stashzilla"
    assert row["status"] == "in_progress"
    assert row["cycle_count"] == 0


def test_json_columns_roundtrip(db: Database):
    sid = db.create_session(
        task_type="feature",
        opus_changes={"files": ["a.py", "b.py"], "commit_sha": "abc123"},
        cost_breakdown={"haiku": 0.05, "opus": 0.52},
    )
    row = db.get_session(sid)
    assert row["opus_changes"]["files"] == ["a.py", "b.py"]
    assert row["cost_breakdown"]["opus"] == 0.52


def test_update_session(db: Database):
    sid = db.create_session(task_type="bug_fix")
    db.update_session(sid, status="awaiting_approval", cycle_count=2,
                      total_cost=1.21)
    row = db.get_session(sid)
    assert row["status"] == "awaiting_approval"
    assert row["cycle_count"] == 2
    assert row["total_cost"] == 1.21


def test_invalid_status_rejected(db: Database):
    with pytest.raises(ValueError):
        db.create_session(task_type="bug_fix", status="bogus")
    sid = db.create_session(task_type="bug_fix")
    with pytest.raises(ValueError):
        db.update_session(sid, status="bogus")


def test_update_missing_raises(db: Database):
    with pytest.raises(KeyError):
        db.update_session("does-not-exist", status="failed")


def test_list_sessions_ordering_and_filter(db: Database):
    a = db.create_session(task_type="bug_fix", project="website")
    b = db.create_session(task_type="feature", project="snipzilla")
    c = db.create_session(task_type="feature", project="website")
    all_rows = db.list_sessions(limit=10)
    assert {r["id"] for r in all_rows} == {a, b, c}
    website = db.list_sessions(project="website")
    assert {r["id"] for r in website} == {a, c}
