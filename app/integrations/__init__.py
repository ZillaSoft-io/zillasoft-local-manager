"""External-context integrations (Sentry, Jira) for Haiku's input phase."""
from .sentry import SentryClient, SentryError
from .jira import JiraClient, JiraError
from .github import GitHubClient, GitHubError

__all__ = ["SentryClient", "SentryError", "JiraClient", "JiraError",
           "GitHubClient", "GitHubError"]
