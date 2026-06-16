"""Shared pytest fixtures for Phase 1 tests."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.config import ConfigHandler

_SAMPLE_ENV = textwrap.dedent(
    """\
    # Sample env for tests
    ANTHROPIC_API_KEY=<FILL>
    GITHUB_TOKEN=secret-token
    STRIPE_SECRET_KEY=sk_test_x
    AUTH0_CLIENT_SECRET=abc

    JIRA_HOST=https://zillasoft-io.atlassian.net
    JIRA_EMAIL=mrodriguez@zillasoft.io
    JIRA_API_TOKEN=jira-tok
    SENTRY_AUTH_TOKEN=sentry-tok
    SENTRY_ORG=zillasoft
    SENTRY_PROJECT_WEBSITE=zillasoft-website

    ANTHROPIC_MODEL_HAIKU=claude-haiku-4-5
    ANTHROPIC_MODEL_SONNET=claude-sonnet-4-6
    ANTHROPIC_MODEL_OPUS=claude-opus-4-8
    ANTHROPIC_EFFORT_HAIKU=medium
    ANTHROPIC_EFFORT_SONNET=medium
    ANTHROPIC_EFFORT_OPUS=high

    PROJECT_WEBSITE_REPO_PATH=C:\\Users\\PC\\Documents\\Mario's Docs\\Prog Projects\\Zillasoft
    PROJECT_WEBSITE_HEALTH_CHECK_FORMAT=html
    PROJECT_SNIPZILLA_HEALTH_CHECK_EXPECTED_STATUS=200

    LOCAL_MANAGER_PORT=5555
    LOCAL_MANAGER_MONTHLY_COST_CAP=100.00
    LOCAL_MANAGER_CURRENT_MONTH_SPENT=0.00
    LOCAL_MANAGER_COST_RESET_MONTH=2026-06
    LOCAL_MANAGER_PAUSE_EXPIRY_DAYS=7
    LOCAL_MANAGER_AUTO_DEPLOY=false
    LOCAL_MANAGER_AUTH_TOKEN=<AUTO>
    NOTIFICATIONS_DESKTOP_ENABLED=false
    NOTIFICATIONS_EMAIL_ENABLED=true
    NOTIFICATIONS_EMAIL_TO=mrodriguez@zillasoft.io
    BREVO_SENDER_EMAIL=noreply@zillasoft.io
    """
)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    p = tmp_path / ".env"
    p.write_text(_SAMPLE_ENV, encoding="utf-8")
    return p


@pytest.fixture
def config(env_file: Path) -> ConfigHandler:
    return ConfigHandler(env_path=env_file)
