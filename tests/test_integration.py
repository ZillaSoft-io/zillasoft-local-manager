"""Phase 10 — end-to-end integration: input -> run -> approve -> deploy.

Wires the full stack (ConversationManager, Orchestrator, ReleaseManager,
DeploymentTracker) with a fake SDK driving every agent, a real bash executor on
a temp git repo, and fake deploy clients. Proves the pieces connect and the
audit trail accumulates across every phase.
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
from app.deploy import DeploymentTracker
from app.execution import CodeExecutor, PreFlight
from app.input import ConversationManager
from app.newapp import NewAppProvisioner
from app.notifications import Notifier
from app.orchestrator import Orchestrator
from app.release import ReleaseManager
from app.vcs import GitOps
from tests.fakes import FakeMessage, FakeSDK, tool_use_message

_NOSLEEP = lambda *_: None


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "t@t.io"], path)
    _run(["git", "config", "user.name", "t"], path)
    (path / "README.md").write_text("hi", encoding="utf-8")
    _run(["git", "add", "-A"], path)
    _run(["git", "commit", "-qm", "init"], path)
    _run(["git", "branch", "-M", "main"], path)


def _bare(path):
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--bare", "-q", str(path)], path.parent)
    return path.as_posix()


# Fake deploy clients (all green).
class _G:
    def poll(self, *a, **k): return {"target": "github_actions", "ok": True}
class _R:
    def poll(self, *a, **k): return {"target": "railway", "ok": True}
class _A:
    def verify_s3(self, b): return {"target": "s3", "ok": True}
    def invalidate(self, d, p=None): return {"target": "cloudfront", "ok": True}
class _H:
    def check(self, p, **k): return {"target": "health_check", "ok": True}


def _responder(task_type="bug_fix", *, fail=False):
    """One responder for every agent across the whole pipeline."""
    op = {"n": 0}

    def r(params):
        model = params["model"]
        if "haiku" in model:
            if "gathering requirements" in (params.get("system") or ""):
                return FakeMessage(json.dumps({
                    "status": "ready", "message": "Ready.",
                    "context_summary": "add passmarker", "scope_level": "uncapped",
                    "monthly_cap": 0, "recommended_stack": "python",
                    "app_name": "TestApp"}))
            return FakeMessage(json.dumps({"approved": True, "corrections": ""}))
        if "opus" in model:
            op["n"] += 1
            if op["n"] % 2 == 1:
                cmd = ("echo noop" if fail else
                       "echo done > passmarker.txt && git add -A && "
                       "git commit -m fix")
                return tool_use_message("run_bash", {"command": cmd})
            return FakeMessage("Done.")
        t = params["messages"][-1]["content"]
        t = t if isinstance(t, str) else ""
        if "DRY-RUN PLAN" in t:
            return FakeMessage("PLAN")
        if "INSTRUCTIONS for Opus" in t:
            return FakeMessage("Create passmarker.txt and commit.")
        return FakeMessage("ok")
    return r


def _stack(config, tmp_path, responder, repo=None):
    db = Database(tmp_path / "i.db")
    audit = AuditTrail(tmp_path / "audit")
    ctrl = SessionController(config, db, audit, pause_dir=tmp_path / "paused")
    ex = CodeExecutor(controller=ctrl)
    pf = PreFlight(config, ex)
    budget = MonthlyBudget(config)
    notifier = Notifier(config, desktop_fn=lambda **k: None)
    sdk = FakeSDK(responder)
    haiku, *_ = build_agents(config, sdk_client=sdk)
    conv = ConversationManager(config, db, audit, haiku,
                               uploads_dir=tmp_path / "uploads")
    orch = Orchestrator(
        config, db, audit, controller=ctrl, executor=ex, preflight=pf,
        budget=budget, notifier=notifier,
        agent_factory=lambda: build_agents(config, sdk_client=sdk),
        provisioner=NewAppProvisioner(config, db, audit), run_existing_tests=False)
    release = ReleaseManager(config, db, audit, ex, notifier, haiku=haiku)
    tracker = DeploymentTracker(config, db, audit, notifier, gha=_G(),
                                railway=_R(), aws=_A(), health=_H(), sleep=_NOSLEEP)
    return dict(db=db, audit=audit, conv=conv, orch=orch, release=release,
                tracker=tracker, ex=ex)


def _configure_website(config, repo, bare):
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    config.set("PROJECT_WEBSITE_TEST_COMMAND", "test -f passmarker.txt",
               actor="agent")
    config.set("PROJECT_WEBSITE_DEPLOY_TRIGGER", "github_actions_manual",
               actor="agent")
    config.set("PROJECT_WEBSITE_GITHUB_ACTIONS_WORKFLOW", "deploy-site.yml",
               actor="agent")
    config.set("PROJECT_WEBSITE_S3_BUCKET", "zillasoft.io", actor="agent")
    config.set("PROJECT_WEBSITE_CLOUDFRONT_DISTRIBUTION", "E1", actor="agent")


# --------------------------- full happy path --------------------------- #
@pytest.mark.parametrize("task_type", ["bug_fix", "feature"])
def test_input_to_deploy_end_to_end(config, tmp_path, task_type):
    repo = tmp_path / "repo"
    _repo(repo)
    bare = _bare(tmp_path / "bare.git")
    _configure_website(config, repo, bare)
    st = _stack(config, tmp_path, _responder(task_type), repo)

    # 1. Input — Haiku clarifies to "ready".
    sid = st["conv"].create_session(task_type, "website")
    turn = st["conv"].handle_message(sid, "add the passmarker fix")
    assert turn.status == "ready"

    # 2. Run the pipeline — Opus writes the file, tests pass.
    result = st["orch"].run_session(sid)
    assert result["status"] == "awaiting_approval"
    assert (repo / "passmarker.txt").exists()

    # 3. Approve — finalize + push to the bare remote.
    st["release"].approve(sid, push_url=bare)
    assert st["db"].get_session(sid)["status"] == "approved"

    # 4. Deploy — all targets green -> deployed.
    dep = st["tracker"].track(sid)
    assert dep["ok"] is True
    assert st["db"].get_session(sid)["status"] == "deployed"

    # 5. Audit trail accumulated every phase.
    audit = st["audit"].read(sid, "website")
    for key in ("preflight", "cycles", "cost_summary", "mario_review",
                "deployment", "deployment_result"):
        assert key in audit, f"missing audit section: {key}"
    assert audit["cost_summary"]["total"] > 0
    assert audit["cycles"][0]["sonnet"]["test_passed"] is True


# --------------------------- escalation path --------------------------- #
def test_cycle_limit_escalates(config, tmp_path):
    repo = tmp_path / "repo"
    _repo(repo)
    _configure_website(config, repo, _bare(tmp_path / "bare.git"))
    st = _stack(config, tmp_path, _responder(fail=True), repo)
    sid = st["conv"].create_session("bug_fix", "website")
    st["conv"].handle_message(sid, "fix it")
    result = st["orch"].run_session(sid)
    assert result["status"] == "escalated"
    s = st["db"].get_session(sid)
    assert s["status"] == "failed" and s["cycle_count"] == 3
    assert "escalation" in st["audit"].read(sid, "website")


# --------------------------- new app provisioning --------------------------- #
def test_new_app_provisions_on_finish(config, tmp_path):
    repo = tmp_path / "site"
    _repo(repo)
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    st = _stack(config, tmp_path, _responder("new_app"), repo)
    from app.agents.usage import Usage, UsageTracker
    sid = st["db"].create_session(task_type="new_app", project=None,
                                  haiku_context={"app_name": "TestApp",
                                                 "recommended_stack": "python"})
    tracker = UsageTracker()
    tracker.record("opus", "claude-opus-4-8", Usage(output_tokens=500))
    result = st["orch"]._finish(st["db"].get_session(sid), None, tracker, 1)
    assert "setup_log" in result
    assert config.get_raw("PROJECT_TESTAPP_FRAMEWORK") == "python_fastapi"
    assert st["db"].get_session(sid)["setup_log"]
