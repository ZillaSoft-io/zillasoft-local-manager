"""Phase 8 — deployment tracking: GHA/Railway/AWS/health clients + tracker."""
from __future__ import annotations

import httpx

from app.audit import AuditTrail
from app.database import Database
from app.deploy import (AwsDeploy, DeploymentTracker, GitHubActionsClient,
                        HealthChecker, RailwayClient)
from app.notifications import Notifier

_NOSLEEP = lambda *_: None


# --------------------------- health check --------------------------- #
def test_health_html_ok(config):
    config.set("PROJECT_WEBSITE_HEALTH_CHECK_URL", "https://zillasoft.io/status",
               actor="agent")
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="ok")))
    hc = HealthChecker(config, http_client=http)
    res = hc.check("website", sleep=_NOSLEEP)
    assert res["ok"] is True


def test_health_json_value_match_and_mismatch(config):
    for k, v in {"PROJECT_SNIPZILLA_HEALTH_CHECK_URL": "https://api.snipzilla.app/health",
                 "PROJECT_SNIPZILLA_HEALTH_CHECK_FORMAT": "json",
                 "PROJECT_SNIPZILLA_HEALTH_CHECK_JSON_KEY": "status",
                 "PROJECT_SNIPZILLA_HEALTH_CHECK_EXPECTED_VALUE": "healthy"}.items():
        config.set(k, v, actor="agent")
    ok_http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "healthy"})))
    assert HealthChecker(config, ok_http).check("snipzilla", sleep=_NOSLEEP)["ok"]
    bad_http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"status": "degraded"})))
    assert HealthChecker(config, bad_http).check(
        "snipzilla", attempts=1, sleep=_NOSLEEP)["ok"] is False


# --------------------------- GitHub Actions poll --------------------------- #
def test_gha_poll_completes(config):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        status = "completed" if state["n"] >= 2 else "in_progress"
        return httpx.Response(200, json={"workflow_runs": [
            {"id": 5, "status": status, "conclusion":
             "success" if status == "completed" else None,
             "html_url": "u"}]})
    http = httpx.Client(base_url="https://api.github.com",
                        transport=httpx.MockTransport(handler))
    gha = GitHubActionsClient(config, http_client=http)
    res = gha.poll("ZillaSoft-io/Snipzilla", "release-windows.yml", "master",
                   attempts=5, delay=0, sleep=_NOSLEEP)
    assert res["ok"] is True and res["status"] == "completed"


# --------------------------- Railway poll --------------------------- #
def test_railway_poll_success(config):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        status = "SUCCESS" if state["n"] >= 2 else "BUILDING"
        return httpx.Response(200, json={"data": {"deployments": {
            "edges": [{"node": {"id": "d1", "status": status}}]}}})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    rc = RailwayClient(config, http_client=http)
    res = rc.poll("proj", "svc", attempts=5, delay=0, sleep=_NOSLEEP)
    assert res["ok"] is True and res["status"] == "SUCCESS"


# --------------------------- AWS --------------------------- #
class _FakeS3:
    def list_objects_v2(self, Bucket, MaxKeys):
        return {"KeyCount": 3}


class _FakeCF:
    def create_invalidation(self, DistributionId, InvalidationBatch):
        return {"Invalidation": {"Id": "I1", "Status": "InProgress"}}


def test_aws_verify_and_invalidate(config):
    aws = AwsDeploy(config, s3_client=_FakeS3(), cloudfront_client=_FakeCF())
    assert aws.verify_s3("zillasoft.io")["ok"] is True
    inv = aws.invalidate("E2TZ5YC9S4W05Q", ["/*"])
    assert inv["ok"] is True and inv["invalidation_id"] == "I1"


# --------------------------- tracker --------------------------- #
class _FakeGHA:
    def __init__(self, ok=True): self.ok = ok
    def poll(self, *a, **k): return {"target": "github_actions", "ok": self.ok}


class _FakeRailway:
    def poll(self, *a, **k): return {"target": "railway", "ok": True}


class _FakeAws:
    def verify_s3(self, b): return {"target": "s3", "ok": True}
    def invalidate(self, d, p=None): return {"target": "cloudfront", "ok": True}


class _FakeHealth:
    def __init__(self, ok=True): self.ok = ok
    def check(self, p, **k): return {"target": "health_check", "ok": self.ok}


def _tracker(config, tmp_path, *, health_ok=True, gha_ok=True):
    db = Database(tmp_path / "d.db")
    audit = AuditTrail(tmp_path / "audit")
    notifier = Notifier(config, desktop_fn=lambda **k: None)
    tr = DeploymentTracker(
        config, db, audit, notifier, gha=_FakeGHA(gha_ok),
        railway=_FakeRailway(), aws=_FakeAws(), health=_FakeHealth(health_ok),
        sleep=_NOSLEEP)
    return tr, db


def test_tracker_railway_deployed(config, tmp_path):
    config.set("PROJECT_SNIPZILLA_DEPLOY_TRIGGER", "railway", actor="agent")
    config.set("PROJECT_SNIPZILLA_GITHUB_ACTIONS_WORKFLOW", "release-windows.yml",
               actor="agent")
    tr, db = _tracker(config, tmp_path)
    sid = db.create_session(task_type="bug_fix", project="snipzilla",
                            status="approved")
    res = tr.track(sid)
    assert res["ok"] is True
    s = db.get_session(sid)
    assert s["status"] == "deployed"
    assert "railway" in s["deployment_status"]["targets"]
    assert "health_check" in s["deployment_status"]["targets"]


def test_tracker_website_targets(config, tmp_path):
    config.set("PROJECT_WEBSITE_DEPLOY_TRIGGER", "github_actions_manual",
               actor="agent")
    config.set("PROJECT_WEBSITE_GITHUB_ACTIONS_WORKFLOW", "deploy-site.yml",
               actor="agent")
    config.set("PROJECT_WEBSITE_S3_BUCKET", "zillasoft.io", actor="agent")
    config.set("PROJECT_WEBSITE_CLOUDFRONT_DISTRIBUTION", "E2TZ5YC9S4W05Q",
               actor="agent")
    tr, db = _tracker(config, tmp_path)
    sid = db.create_session(task_type="feature", project="website",
                            status="approved")
    res = tr.track(sid)
    assert res["ok"] is True
    targets = db.get_session(sid)["deployment_status"]["targets"]
    assert {"github_actions", "s3", "cloudfront", "health_check"} <= set(targets)


def test_tracker_failure_marks_failed(config, tmp_path):
    config.set("PROJECT_SNIPZILLA_DEPLOY_TRIGGER", "railway", actor="agent")
    tr, db = _tracker(config, tmp_path, health_ok=False)
    sid = db.create_session(task_type="bug_fix", project="snipzilla",
                            status="approved")
    res = tr.track(sid)
    assert res["ok"] is False
    s = db.get_session(sid)
    assert s["status"] == "failed"
    assert "rollback" in s["error_message"]


def test_tracker_skips_new_app(config, tmp_path):
    tr, db = _tracker(config, tmp_path)
    sid = db.create_session(task_type="new_app", project=None)
    res = tr.track(sid)
    assert res["ok"] is False and "skipped" in res
