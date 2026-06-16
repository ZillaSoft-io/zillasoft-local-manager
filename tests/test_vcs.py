"""Phase 6 — git ops + release (approve/reject/rollback) on real repos."""
from __future__ import annotations

import subprocess

import pytest

from app.audit import AuditTrail
from app.database import Database
from app.execution import CodeExecutor
from app.notifications import Notifier
from app.release import ReleaseManager
from app.vcs import GitOps, generate_commit_message


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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


def _git(repo):
    return GitOps(str(repo), CodeExecutor())


# --------------------------- GitOps --------------------------- #
def test_gitops_basics(tmp_path):
    repo = tmp_path / "r"
    _repo(repo)
    g = _git(repo)
    assert g.current_branch() == "main"
    assert g.is_clean() is True
    assert g.has_remote() is False
    base = g.head_sha()

    (repo / "a.txt").write_text("x", encoding="utf-8")
    assert g.is_clean() is False
    sha = g.commit("feat: add a")
    assert sha and sha != base
    assert g.is_clean() is True


def test_gitops_push_revert_reset(tmp_path):
    repo = tmp_path / "r"
    _repo(repo)
    bare = _bare(tmp_path / "bare.git")
    g = _git(repo)
    base = g.head_sha()

    (repo / "a.txt").write_text("x", encoding="utf-8")
    sha = g.commit("feat: a")
    g.push("main", url=bare)
    ls = subprocess.run(["git", "ls-remote", bare], capture_output=True,
                        text=True)
    assert "refs/heads/main" in ls.stdout

    g.revert(sha)
    assert g.head_sha() != sha            # revert created a new commit
    g.reset_hard(base)
    assert g.head_sha() == base           # back to the start


# --------------------------- commit message --------------------------- #
def test_commit_message_fallback():
    assert generate_commit_message("", haiku=None).startswith("chore")
    assert generate_commit_message("1 file changed", haiku=None).startswith("chore")


# --------------------------- ReleaseManager --------------------------- #
@pytest.fixture
def release(config, tmp_path):
    db = Database(tmp_path / "rel.db")
    audit = AuditTrail(tmp_path / "audit")
    notifier = Notifier(config, desktop_fn=lambda **k: None)
    rm = ReleaseManager(config, db, audit, CodeExecutor(), notifier, haiku=None)
    return rm, db


def test_approve_finalizes_and_pushes(release, config, tmp_path):
    rm, db = release
    repo = tmp_path / "work"
    _repo(repo)
    bare = _bare(tmp_path / "bare.git")
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    base = _git(repo).head_sha()
    sid = db.create_session(task_type="bug_fix", project="website",
                            status="awaiting_approval",
                            deployment_status={"base_sha": base})
    (repo / "fix.txt").write_text("done", encoding="utf-8")  # uncommitted
    res = rm.approve(sid, push_url=bare)
    assert res["status"] == "approved"
    s = db.get_session(sid)
    assert s["status"] == "approved"
    assert s["mario_approved"] in (1, True)
    assert s["deployment_status"]["github_commit"]["sha"]
    ls = subprocess.run(["git", "ls-remote", bare], capture_output=True,
                        text=True)
    assert "refs/heads/main" in ls.stdout


def test_reject_discards_local_commits(release, config, tmp_path):
    rm, db = release
    repo = tmp_path / "work"
    _repo(repo)
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    g = _git(repo)
    base = g.head_sha()
    (repo / "x.txt").write_text("x", encoding="utf-8")
    g.commit("opus change")           # session's local commit
    assert g.head_sha() != base
    sid = db.create_session(task_type="bug_fix", project="website",
                            status="awaiting_approval",
                            deployment_status={"base_sha": base})
    rm.reject(sid, notes="not right")
    assert g.head_sha() == base        # local commit discarded
    assert db.get_session(sid)["status"] == "failed"


def test_rollback_reverts_and_pushes(release, config, tmp_path):
    rm, db = release
    repo = tmp_path / "work"
    _repo(repo)
    bare = _bare(tmp_path / "bare.git")
    config.set("PROJECT_WEBSITE_REPO_PATH", str(repo), actor="agent")
    g = _git(repo)
    g.push("main", url=bare)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    sha = g.commit("feat: f")
    g.push("main", url=bare)
    sid = db.create_session(task_type="bug_fix", project="website",
                            status="approved",
                            deployment_status={"github_commit":
                                               {"sha": sha, "branch": "main"}})
    res = rm.rollback(sid, push_url=bare)
    assert res["status"] == "rolled_back"
    assert res["reverted_sha"] == sha
    assert g.head_sha() != sha
    assert db.get_session(sid)["status"] == "rolled_back"
