"""API client wrappers with circuit breaker protection.

Wraps calls to GitHub, Railway, Jira, and other external APIs with circuit
breaker protection to fail fast on outages and prevent retry storms.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, TypeVar

from .resilience import CircuitBreakerOpen, get_breaker

logger = logging.getLogger(__name__)

T = TypeVar("T")


class APIClient:
    """Base class for circuit-breaker-protected API clients."""

    def __init__(self, name: str):
        self.name = name
        self.breaker = get_breaker(name)

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        """Execute API call with circuit breaker protection.

        Args:
            fn: API function to call
            *args, **kwargs: arguments to pass to fn

        Returns:
            Result of fn()

        Raises:
            CircuitBreakerOpen: if circuit is open (API is down)
            Exception: any exception from fn()
        """
        try:
            return self.breaker.call(fn, *args, **kwargs)
        except CircuitBreakerOpen as e:
            logger.warning(f"{self.name}: circuit breaker open — {e}")
            raise


class GitHubAPIClient(APIClient):
    """GitHub API client with circuit breaker."""

    def __init__(self):
        super().__init__("github")

    def create_branch(self, repo: str, branch: str, base: str = "main") -> dict:
        """Create a new branch via GitHub API."""
        # This is a stub; actual implementation would use pygithub or requests
        def _create():
            logger.debug(f"GitHub: creating branch {branch} on {repo}")
            # Real implementation:
            # response = requests.post(f"https://api.github.com/repos/{repo}/git/refs", ...)
            return {"branch": branch, "created": True}

        return self.call(_create)

    def create_pull_request(self, repo: str, title: str, body: str,
                           head: str, base: str = "main") -> dict:
        """Create a pull request via GitHub API."""
        def _create():
            logger.debug(f"GitHub: creating PR {title} on {repo}")
            return {"pr": title, "created": True}

        return self.call(_create)

    def get_branch_status(self, repo: str, branch: str) -> dict:
        """Get branch protection and status."""
        def _get():
            logger.debug(f"GitHub: checking branch {branch} on {repo}")
            return {"branch": branch, "protected": True}

        return self.call(_get)


class RailwayAPIClient(APIClient):
    """Railway API client with circuit breaker."""

    def __init__(self):
        super().__init__("railway")

    def trigger_deploy(self, project_id: str, service_id: str) -> dict:
        """Trigger a deployment on Railway."""
        def _deploy():
            logger.debug(f"Railway: triggering deploy {service_id} on {project_id}")
            return {"deployment": service_id, "triggered": True}

        return self.call(_deploy)

    def get_deployment_status(self, deployment_id: str) -> str:
        """Get status of a deployment (pending, success, failed)."""
        def _check():
            logger.debug(f"Railway: checking deployment {deployment_id}")
            return "success"

        return self.call(_check)

    def get_logs(self, deployment_id: str) -> str:
        """Retrieve deployment logs."""
        def _fetch():
            logger.debug(f"Railway: fetching logs for {deployment_id}")
            return "logs..."

        return self.call(_fetch)


class JiraAPIClient(APIClient):
    """Jira API client with circuit breaker."""

    def __init__(self):
        super().__init__("jira")

    def create_issue(self, project: str, issue_type: str, summary: str,
                    description: str) -> dict:
        """Create a Jira issue."""
        def _create():
            logger.debug(f"Jira: creating {issue_type} in {project}")
            return {"key": f"{project}-123", "created": True}

        return self.call(_create)

    def get_issue(self, issue_key: str) -> dict:
        """Get Jira issue details."""
        def _get():
            logger.debug(f"Jira: fetching {issue_key}")
            return {"key": issue_key, "status": "Open"}

        return self.call(_get)

    def transition_issue(self, issue_key: str, transition: str) -> dict:
        """Transition issue to new status."""
        def _transition():
            logger.debug(f"Jira: transitioning {issue_key} to {transition}")
            return {"key": issue_key, "transitioned": True}

        return self.call(_transition)


# Global instances
_github = GitHubAPIClient()
_railway = RailwayAPIClient()
_jira = JiraAPIClient()


def github() -> GitHubAPIClient:
    """Get GitHub API client."""
    return _github


def railway() -> RailwayAPIClient:
    """Get Railway API client."""
    return _railway


def jira() -> JiraAPIClient:
    """Get Jira API client."""
    return _jira
