"""Phase 5 — orchestrator: full pipeline loop on a real temp git repo.

Opus actually writes a file via the bash tool; tests are a trivial shell check
(`test -f passmarker.txt`) so the loop is deterministic without a python/pytest
env in the target repo.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app.agents import build_agents
from app.audit import AuditTrail
from app.control import SessionController
from app.cost import MonthlyBudget
from app.database import Database
from app.execution import CodeExecutor, PreFlight
from app.notifications import Notifier
from app.orchestrator import Orchestrator
from tests.fakes import FakeMessage, FakeSDK, tool_use_message


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.io"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def _build(config, tmp_path, responder, repo):
    db = Database(tmp_path / "o.db")
    audit = AuditTrail(tmp_path / "audit")
    ctrl = SessionController(config, db, audit, pause_dir=tmp_path / "paused")
    ex = CodeExecutor(controller=ctrl)
    pf = PreFlight(config, ex)
    budget = MonthlyBudget(config)
    notifier = Notifier(config, desktop_fn=lambda **k: None)
    sdk = FakeSDK(responder)
    orch = Orchestrator(
        config, db, audit, controller=ctrl, executor=ex, preflight=pf,
        budget=budget, notifier=notifier,
        agent_factory=lambda: build_agents(config, sdk_client=sdk),
        run_existing_tests=False)
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    config.set("PROJECT_WEBSITE_TEST_COMMAND", "test -f passmarker.txt",
               actor="agent")
    return orch, db, ctrl


def _sonnet_text(params):
    u = params["messages"][-1]["content"]
    return u if isinstance(u, str) else ""


# --------------------------- happy path --------------------------- #
def _responder_pass():
    op = {"n": 0}

    def r(params):
        model = params["model"]
        if "haiku" in model:
            return FakeMessage(json.dumps({"approved": True, "corrections": ""}))
        if "opus" in model:
            op["n"] += 1
            if op["n"] == 1:
                return tool_use_message("run_bash", {
                    "command": "echo done > passmarker.txt && "
                               "git add -A && git commit -m fix"})
            return FakeMessage("Committed passmarker.txt.")
        t = _sonnet_text(params)
        if "DRY-RUN PLAN" in t:
            return FakeMessage("PLAN: create passmarker.txt")
        if "INSTRUCTIONS for Opus" in t:
            return FakeMessage("Create passmarker.txt and commit.")
        if "Summarize Opus" in t:
            return FakeMessage("Created passmarker and committed.")
        if "Review the change" in t:
            return FakeMessage("Correct; tests pass.")
        return FakeMessage("ok")
    return r


def test_full_cycle_passes_and_awaits_approval(config, tmp_path):
    repo = tmp_path / "repo"
    _git_repo(repo)
    orch, db, ctrl = _build(config, tmp_path, _responder_pass(), repo)
    sid = db.create_session(task_type="bug_fix", project="website",
                            haiku_context={"summary": "add passmarker"})
    result = orch.run_session(sid)
    assert result["status"] == "awaiting_approval"
    assert (repo / "passmarker.txt").exists()
    s = db.get_session(sid)
    assert s["status"] == "awaiting_approval"
    assert s["cycle_count"] == 1
    assert s["total_cost"] > 0
    assert s["opus_changes"]["commit_sha"]  # captured after git commit


# --------------------------- escalation --------------------------- #
def _responder_fail():
    op = {"n": 0}

    def r(params):
        model = params["model"]
        if "haiku" in model:
            return FakeMessage(json.dumps({"approved": True, "corrections": ""}))
        if "opus" in model:
            op["n"] += 1
            if op["n"] % 2 == 1:  # tool call that does nothing useful
                return tool_use_message("run_bash", {"command": "echo noop"})
            return FakeMessage("Tried.")
        t = _sonnet_text(params)
        if "DRY-RUN PLAN" in t:
            return FakeMessage("PLAN")
        if "INSTRUCTIONS for Opus" in t:
            return FakeMessage("Create passmarker.txt.")
        if "tests failed" in t.lower():
            return FakeMessage("Try again: create the file.")
        return FakeMessage("noted")
    return r


def test_escalates_after_cycle_limit(config, tmp_path):
    repo = tmp_path / "repo"
    _git_repo(repo)
    orch, db, ctrl = _build(config, tmp_path, _responder_fail(), repo)
    sid = db.create_session(task_type="bug_fix", project="website",
                            haiku_context={"summary": "add passmarker"})
    result = orch.run_session(sid)
    assert result["status"] == "escalated"
    s = db.get_session(sid)
    assert s["status"] == "failed"
    assert s["cycle_count"] == 3
    assert "cycle limit" in s["error_message"]


# --------------------------- kill mid-run --------------------------- #
def test_stop_signal_halts_before_cycle(config, tmp_path):
    repo = tmp_path / "repo"
    _git_repo(repo)
    orch, db, ctrl = _build(config, tmp_path, _responder_pass(), repo)
    sid = db.create_session(task_type="bug_fix", project="website",
                            haiku_context={"summary": "add passmarker"})
    ctrl.request_stop(sid)   # kill before the loop starts
    result = orch.run_session(sid)
    assert result["status"] == "stopped"
    assert db.get_session(sid)["status"] == "stopped"
