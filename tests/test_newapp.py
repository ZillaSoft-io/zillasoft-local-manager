"""Phase 7 — new-app auto-config: GitHub repo, .env section, setup log."""
from __future__ import annotations

import httpx

from app.agents.usage import Usage, UsageTracker
from app.audit import AuditTrail
from app.cost import MonthlyBudget
from app.database import Database
from app.integrations import GitHubClient
from app.newapp import NewAppProvisioner
from app.notifications import Notifier
from app.orchestrator import Orchestrator


# --------------------------- GitHub client --------------------------- #
def test_github_create_repo_mocked(config):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/orgs/ZillaSoft-io/repos"
        return httpx.Response(201, json={
            "full_name": "ZillaSoft-io/snipzillamobile",
            "html_url": "https://github.com/ZillaSoft-io/snipzillamobile",
            "clone_url": "https://github.com/ZillaSoft-io/snipzillamobile.git",
            "default_branch": "main",
        })
    http = httpx.Client(base_url="https://api.github.com",
                        transport=httpx.MockTransport(handler))
    gh = GitHubClient(config, http_client=http)
    info = gh.create_repo("snipzillamobile")
    assert info["slug"] == "ZillaSoft-io/snipzillamobile"
    assert info["clone_url"].endswith(".git")


# --------------------------- provisioner --------------------------- #
def _prov(config, tmp_path, github=None):
    db = Database(tmp_path / "n.db")
    audit = AuditTrail(tmp_path / "audit")
    return NewAppProvisioner(config, db, audit, github=github), db, audit


def test_register_env_section_python(config, tmp_path):
    prov, _, _ = _prov(config, tmp_path)
    keys = prov.register_env_section("SnipzillaMobile", "Python + FastAPI")
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_FRAMEWORK") == "python_fastapi"
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_TEST_COMMAND") == "pytest tests/ -v"
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_DEPLOY_TRIGGER") == "railway"
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_RAILWAY_PROJECT_ID") == "<FILL>"
    assert "PROJECT_SNIPZILLAMOBILE_GITHUB_REPO" in keys


def test_register_env_section_astro(config, tmp_path):
    prov, _, _ = _prov(config, tmp_path)
    prov.register_env_section("MarketingSite", "Astro + TypeScript")
    assert config.get_raw("PROJECT_MARKETINGSITE_FRAMEWORK") == "astro"
    assert config.get_raw("PROJECT_MARKETINGSITE_BUILD_COMMAND") == "pnpm build"
    assert config.get_raw("PROJECT_MARKETINGSITE_DEPLOY_TRIGGER") == \
        "github_actions_manual"


def test_setup_log_content(config, tmp_path):
    prov, _, _ = _prov(config, tmp_path)
    log = prov.generate_setup_log("SnipzillaMobile", "Python + FastAPI",
                                  "ZillaSoft-io/snipzillamobile")
    assert "SnipzillaMobile" in log
    assert "Next steps" in log
    assert "Railway" in log
    assert "snipzillamobile" in log


def test_provision_sets_setup_log_and_env(config, tmp_path):
    prov, db, audit = _prov(config, tmp_path)
    sid = db.create_session(task_type="new_app", project=None, haiku_context={
        "app_name": "SnipzillaMobile", "recommended_stack": "Python + FastAPI",
        "summary": "iOS backend"})
    session = db.get_session(sid)
    result = prov.provision(session, create_repo=False)
    assert "SnipzillaMobile" in result["setup_log"]
    assert db.get_session(sid)["setup_log"]
    assert audit.read(sid, None)["new_app"]["name"] == "SnipzillaMobile"
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_FRAMEWORK") == "python_fastapi"


def test_provision_create_repo(config, tmp_path):
    def handler(request):
        return httpx.Response(201, json={
            "full_name": "ZillaSoft-io/snipzillamobile",
            "html_url": "u", "clone_url": "c.git", "default_branch": "main"})
    http = httpx.Client(base_url="https://api.github.com",
                        transport=httpx.MockTransport(handler))
    prov, db, audit = _prov(config, tmp_path, github=GitHubClient(config, http))
    sid = db.create_session(task_type="new_app", project=None, haiku_context={
        "app_name": "SnipzillaMobile", "recommended_stack": "python"})
    result = prov.provision(db.get_session(sid), create_repo=True)
    assert result["repo"]["slug"] == "ZillaSoft-io/snipzillamobile"
    assert config.get_raw("PROJECT_SNIPZILLAMOBILE_GITHUB_REPO") == \
        "ZillaSoft-io/snipzillamobile"


# --------------------------- orchestrator hook --------------------------- #
class _FakeProvisioner:
    def __init__(self):
        self.called = False

    def provision(self, session, **kw):
        self.called = True
        return {"setup_log": "SETUP LOG", "name": "x", "repo": None,
                "env_keys": {}}


def test_orchestrator_finish_provisions_new_app(config, tmp_path):
    db = Database(tmp_path / "o.db")
    audit = AuditTrail(tmp_path / "audit")
    fp = _FakeProvisioner()
    orch = Orchestrator(
        config, db, audit, controller=None, executor=None, preflight=None,
        budget=MonthlyBudget(config),
        notifier=Notifier(config, desktop_fn=lambda **k: None),
        agent_factory=lambda: None, provisioner=fp)
    sid = db.create_session(task_type="new_app", project=None,
                            haiku_context={"app_name": "X"})
    tracker = UsageTracker()
    tracker.record("opus", "claude-opus-4-8", Usage(output_tokens=1000))
    result = orch._finish(db.get_session(sid), None, tracker, 1)
    assert fp.called is True
    assert result["setup_log"] == "SETUP LOG"
    assert db.get_session(sid)["status"] == "awaiting_approval"
