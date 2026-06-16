"""Phase 1 — AuditTrail tests: write/read/update/append, atomic, new-app path."""
from __future__ import annotations

from app.audit import AuditTrail


def test_write_and_read(tmp_path):
    audit = AuditTrail(tmp_path)
    path = audit.write("sess-1", "stashzilla", {"task_type": "bug_fix"})
    assert path.exists()
    assert path.parent.name == "stashzilla"
    data = audit.read("sess-1", "stashzilla")
    assert data["task_type"] == "bug_fix"


def test_new_app_uses_placeholder_folder(tmp_path):
    audit = AuditTrail(tmp_path)
    path = audit.write("sess-2", None, {"task_type": "new_app"})
    assert path.parent.name == "_new_app"


def test_update_merges(tmp_path):
    audit = AuditTrail(tmp_path)
    audit.write("s", "website", {"a": 1})
    audit.update("s", "website", {"b": 2})
    data = audit.read("s", "website")
    assert data == {"a": 1, "b": 2}


def test_append_cycle(tmp_path):
    audit = AuditTrail(tmp_path)
    audit.write("s", "snipzilla", {"cycles": []})
    audit.append_cycle("s", "snipzilla", {"cycle_num": 1})
    audit.append_cycle("s", "snipzilla", {"cycle_num": 2})
    data = audit.read("s", "snipzilla")
    assert [c["cycle_num"] for c in data["cycles"]] == [1, 2]
