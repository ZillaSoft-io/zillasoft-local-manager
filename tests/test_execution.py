"""Phase 5 — execution: bash executor, test parsing, pre-flight."""
from __future__ import annotations

import subprocess

import pytest

from app.control import SessionController
from app.audit import AuditTrail
from app.database import Database
from app.execution import (CodeExecutor, CommandStopped, PreFlight,
                           parse_test_output, run_tests)


# --------------------------- executor --------------------------- #
def test_executor_runs_command(tmp_path):
    ex = CodeExecutor()
    r = ex.run("echo hello", cwd=tmp_path)
    assert r.ok is True
    assert "hello" in r.stdout


def test_executor_nonzero_exit(tmp_path):
    ex = CodeExecutor()
    r = ex.run("exit 3", cwd=tmp_path)
    assert r.returncode == 3
    assert r.ok is False


def test_executor_cancelled_by_stop_signal(config, tmp_path):
    db = Database(tmp_path / "x.db")
    audit = AuditTrail(tmp_path / "audit")
    ctrl = SessionController(config, db, audit, pause_dir=tmp_path / "p")
    ex = CodeExecutor(controller=ctrl)
    sid = db.create_session(task_type="bug_fix")
    ctrl.request_stop(sid)
    with pytest.raises(CommandStopped):
        ex.run("echo x", cwd=tmp_path, session_id=sid)


# --------------------------- test parsing --------------------------- #
def test_parse_pytest_pass():
    tr = parse_test_output("==== 34 passed in 0.5s ====", returncode=0)
    assert tr.ok is True and tr.passed == 34


def test_parse_pytest_fail():
    tr = parse_test_output("1 failed, 33 passed", returncode=1)
    assert tr.ok is False and tr.failed == 1 and tr.passed == 33


def test_run_tests_via_executor(tmp_path):
    ex = CodeExecutor()
    assert run_tests(ex, str(tmp_path), "exit 0").ok is True
    assert run_tests(ex, str(tmp_path), "exit 1").ok is False
    assert run_tests(ex, str(tmp_path), "").ok is True   # no command


# --------------------------- preflight --------------------------- #
def _git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.io"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def test_preflight_startup_reports_credentials(config, tmp_path):
    pf = PreFlight(config, CodeExecutor())
    report = pf.startup()
    assert "credentials_present" in report
    assert report["credentials_present"]["ANTHROPIC_API_KEY"] is False  # <FILL>


def test_preflight_clean_repo(config, tmp_path):
    _git_repo(tmp_path)
    pf = PreFlight(config, CodeExecutor())
    res = pf.session(repo_path=str(tmp_path), test_command="",
                     run_existing_tests=False)
    assert res.checks["git_clean"] is True
    # ANTHROPIC_API_KEY is <FILL> -> model key missing -> warns + not ok
    assert res.checks["model_key_present"] is False
    assert any("ANTHROPIC_API_KEY" in w for w in res.warnings)


def test_preflight_dirty_repo_warns(config, tmp_path):
    _git_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("x", encoding="utf-8")
    pf = PreFlight(config, CodeExecutor())
    res = pf.session(repo_path=str(tmp_path), test_command="",
                     run_existing_tests=False)
    assert res.checks["git_clean"] is False
    assert any("uncommitted" in w for w in res.warnings)
