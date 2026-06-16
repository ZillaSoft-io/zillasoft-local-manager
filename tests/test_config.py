"""Phase 1 — ConfigHandler tests: tiered writes, typed reads, atomic preserve."""
from __future__ import annotations

import pytest

from app.config import ConfigHandler
from app.errors import ConfigValidationError, CredentialWriteError


# --------------------------- credential protection --------------------------- #
@pytest.mark.parametrize("key", [
    "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "STRIPE_SECRET_KEY",
    "AUTH0_CLIENT_SECRET", "LOCAL_MANAGER_AUTH_TOKEN",
])
def test_agent_cannot_write_credentials(config: ConfigHandler, key: str):
    with pytest.raises(CredentialWriteError):
        config.set(key, "new-value")  # actor defaults to 'agent'


def test_system_actor_can_write_credentials(config: ConfigHandler):
    config.set("LOCAL_MANAGER_AUTH_TOKEN", "tok123", actor="system")
    assert config.get_raw("LOCAL_MANAGER_AUTH_TOKEN") == "tok123"


def test_is_credential_classification():
    assert ConfigHandler.is_credential("AWS_SECRET_ACCESS_KEY")
    assert ConfigHandler.is_credential("STRIPE_ANYTHING")
    assert not ConfigHandler.is_credential("PROJECT_WEBSITE_GITHUB_BRANCH")


def test_agent_can_write_project_config(config: ConfigHandler):
    config.set("PROJECT_WEBSITE_HEALTH_CHECK_FORMAT", "json")
    assert config.get("PROJECT_WEBSITE_HEALTH_CHECK_FORMAT") == "json"


# --------------------------- typed reads --------------------------- #
def test_typed_reads(config: ConfigHandler):
    assert config.get("LOCAL_MANAGER_PORT") == 5555
    assert isinstance(config.get("LOCAL_MANAGER_PORT"), int)
    assert config.get("LOCAL_MANAGER_MONTHLY_COST_CAP") == 100.0
    assert config.get("LOCAL_MANAGER_AUTO_DEPLOY") is False
    assert config.get("NOTIFICATIONS_EMAIL_ENABLED") is True
    assert config.get("NOTIFICATIONS_EMAIL_TO") == "mrodriguez@zillasoft.io"


def test_unset_sentinels_return_default(config: ConfigHandler):
    # ANTHROPIC_API_KEY is <FILL>; get() should treat it as unset.
    assert config.get("ANTHROPIC_API_KEY", default="MISSING") == "MISSING"
    assert config.is_set(config.get_raw("ANTHROPIC_API_KEY")) is False
    assert config.is_set(config.get_raw("GITHUB_TOKEN")) is True


def test_require_raises_when_unset(config: ConfigHandler):
    with pytest.raises(ConfigValidationError):
        config.require("ANTHROPIC_API_KEY")
    assert config.require("GITHUB_TOKEN") == "secret-token"


# --------------------------- validation on write --------------------------- #
def test_enum_validation_on_write(config: ConfigHandler):
    with pytest.raises(ConfigValidationError):
        config.set("ANTHROPIC_EFFORT_OPUS", "ludicrous")
    config.set("ANTHROPIC_EFFORT_OPUS", "low")  # valid
    assert config.get("ANTHROPIC_EFFORT_OPUS") == "low"


def test_int_validation_on_write(config: ConfigHandler):
    with pytest.raises(ConfigValidationError):
        config.set("LOCAL_MANAGER_PORT", "not-a-number")
    config.set("LOCAL_MANAGER_PORT", 6000)
    assert config.get("LOCAL_MANAGER_PORT") == 6000


# --------------------------- atomic write preserves format --------------------------- #
def test_write_preserves_comments_and_other_keys(config: ConfigHandler, env_file):
    config.set("LOCAL_MANAGER_PORT", 7777)
    text = env_file.read_text(encoding="utf-8")
    assert "# Sample env for tests" in text          # comment preserved
    assert "LOCAL_MANAGER_PORT=7777" in text          # value updated
    assert "JIRA_HOST=https://zillasoft-io.atlassian.net" in text  # untouched


def test_windows_path_preserved_verbatim(config: ConfigHandler):
    # Backslashes and the apostrophe in the path must survive a reload.
    raw = config.get_raw("PROJECT_WEBSITE_REPO_PATH")
    assert raw == "C:\\Users\\PC\\Documents\\Mario's Docs\\Prog Projects\\Zillasoft"
    p = config.resolve_path("PROJECT_WEBSITE_REPO_PATH")
    assert p.name == "Zillasoft"


def test_append_new_key(config: ConfigHandler, env_file):
    config.set("PROJECT_NEWAPP_GITHUB_BRANCH", "main")
    config.reload()
    assert config.get_raw("PROJECT_NEWAPP_GITHUB_BRANCH") == "main"


def test_snapshot_masks_credentials(config: ConfigHandler):
    snap = config.snapshot(redact_credentials=True)
    assert snap["GITHUB_TOKEN"] == "<set>"
    assert snap["ANTHROPIC_API_KEY"] == "<unset>"   # was <FILL>
    assert snap["JIRA_HOST"] == "https://zillasoft-io.atlassian.net"


def test_ensure_auth_token_generates_once(config: ConfigHandler):
    token = config.ensure_auth_token()
    assert config.is_set(token)
    # Second call returns the same token (idempotent).
    assert config.ensure_auth_token() == token
