"""Daily changelog aggregator for all ZillaSoft projects.

Scans git commits from all projects (Local Manager, Snipzilla, Website, Stashzilla),
summarizes with Haiku, and posts daily entries to the website changelog.
Prevents spam by batching all daily changes into one entry.
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ChangelogUpdater:
    """Aggregates commits and posts daily to website changelog."""

    def __init__(self, projects: dict[str, str], website_repo: str, haiku_agent=None):
        """
        Args:
            projects: dict of {project_name: repo_path}
            website_repo: path to zillasoft.io website repo
            haiku_agent: Haiku agent for summarization (optional)
        """
        self.projects = projects
        self.website_repo = Path(website_repo)
        self.haiku = haiku_agent
        self.state_file = Path(".last_changelog_update.json")

    def _load_state(self) -> dict:
        """Load last update timestamp."""
        if not self.state_file.exists():
            return {"last_update": None, "last_date": None}
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception:
            return {"last_update": None, "last_date": None}

    def _save_state(self, timestamp: str, date: str) -> None:
        """Save last update timestamp."""
        with open(self.state_file, "w") as f:
            json.dump({"last_update": timestamp, "last_date": date}, f)

    def _get_commits_since(self, repo_path: str, since_timestamp: Optional[str]) -> list[dict]:
        """Get commits from a repo since timestamp."""
        try:
            if since_timestamp:
                # Get commits since the timestamp
                cmd = [
                    "git", "-C", repo_path, "log",
                    f"--since={since_timestamp}",
                    "--format=%H%n%an%n%aI%n%s%n%b%n---END---"
                ]
            else:
                # First run: get commits from last 7 days
                since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                cmd = [
                    "git", "-C", repo_path, "log",
                    f"--since={since}",
                    "--format=%H%n%an%n%aI%n%s%n%b%n---END---"
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.warning(f"Failed to get commits from {repo_path}")
                return []

            commits = []
            blocks = result.stdout.split("---END---")
            for block in blocks:
                lines = block.strip().split("\n")
                if len(lines) >= 3:
                    commits.append({
                        "hash": lines[0],
                        "author": lines[1],
                        "timestamp": lines[2],
                        "subject": lines[3] if len(lines) > 3 else "",
                        "body": "\n".join(lines[4:]) if len(lines) > 4 else "",
                    })
            return commits
        except Exception as e:
            logger.error(f"Error getting commits from {repo_path}: {e}")
            return []

    def _categorize_commits(self, commits: list[dict]) -> dict[str, list[dict]]:
        """Categorize commits by type (bug, feature, improvement)."""
        categories = {
            "bugs": [],
            "features": [],
            "improvements": [],
            "announcements": [],
        }

        for commit in commits:
            subject = (commit["subject"] or "").lower()
            body = (commit["body"] or "").lower()
            full_text = subject + " " + body

            if any(word in subject for word in ["fix", "bug", "hotfix", "patch"]):
                categories["bugs"].append(commit)
            elif any(word in subject for word in ["feat", "add", "implement", "new"]):
                categories["features"].append(commit)
            elif any(word in subject for word in ["announce", "launch", "release"]):
                categories["announcements"].append(commit)
            else:
                categories["improvements"].append(commit)

        return categories

    def _summarize_with_haiku(self, commits: list[dict]) -> Optional[str]:
        """Use Haiku to summarize a list of commits."""
        if not self.haiku or not commits:
            return None

        commit_text = "\n".join([
            f"- {c['subject']}" for c in commits[:10]  # Limit to 10 commits for context
        ])

        try:
            prompt = f"""Summarize these ZillaSoft commits into 1-2 sentences for a changelog entry.
Be specific but concise. Focus on user impact.

Commits:
{commit_text}

Changelog entry (1-2 sentences):"""

            response = self.haiku.invoke(prompt, max_tokens=100)
            return response.strip() if response else None
        except Exception as e:
            logger.warning(f"Failed to summarize with Haiku: {e}")
            return None

    def should_update(self) -> bool:
        """Check if 24 hours have passed since last update."""
        state = self._load_state()
        if not state.get("last_update"):
            return True

        try:
            last = datetime.fromisoformat(state["last_update"])
            now = datetime.now(timezone.utc)
            return (now - last).total_seconds() >= 86400  # 24 hours
        except Exception:
            return True

    def get_daily_changelog_entry(self, project_commits: dict[str, list[dict]]) -> Optional[str]:
        """Generate a changelog markdown entry for the day."""
        all_commits = []
        for project, commits in project_commits.items():
            all_commits.extend(commits)

        if not all_commits:
            return None

        # Categorize
        categories = self._categorize_commits(all_commits)

        # Build entry
        lines = []

        if categories["announcements"]:
            summary = self._summarize_with_haiku(categories["announcements"])
            if summary:
                lines.append(f"**New:** {summary}")

        if categories["features"]:
            summary = self._summarize_with_haiku(categories["features"])
            if summary:
                lines.append(f"**Features:** {summary}")

        if categories["bugs"]:
            summary = self._summarize_with_haiku(categories["bugs"])
            if summary:
                lines.append(f"**Fixes:** {summary}")

        if categories["improvements"]:
            summary = self._summarize_with_haiku(categories["improvements"])
            if summary:
                lines.append(f"**Improvements:** {summary}")

        return "\n\n".join(lines) if lines else None

    def update_changelog(self) -> bool:
        """Scan all projects, summarize, and post to website changelog."""
        if not self.should_update():
            logger.info("Changelog updated recently, skipping")
            return False

        logger.info("Starting daily changelog update...")

        # Get state
        state = self._load_state()
        last_update = state.get("last_update")

        # Collect commits from all projects
        project_commits = {}
        for project_name, repo_path in self.projects.items():
            commits = self._get_commits_since(repo_path, last_update)
            if commits:
                project_commits[project_name] = commits
                logger.info(f"{project_name}: {len(commits)} commits since last update")

        if not project_commits:
            logger.info("No new commits found")
            return False

        # Generate entry
        entry = self.get_daily_changelog_entry(project_commits)
        if not entry:
            logger.warning("Failed to generate changelog entry")
            return False

        # Create markdown file for Astro
        today = datetime.now(timezone.utc).date()
        filename = f"{today}.md"  # Astro will use this for slug
        filepath = self.website_repo / "src" / "content" / "blog" / filename

        frontmatter = f"""---
title: "{today.strftime('%B %d, %Y')} Updates"
description: "Daily changelog for ZillaSoft products"
pubDate: "{today.isoformat()}T00:00:00Z"
---

{entry}
"""

        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w") as f:
                f.write(frontmatter)
            logger.info(f"Created changelog entry: {filepath}")

            # Commit to website repo
            subprocess.run(
                ["git", "-C", str(self.website_repo), "add", str(filepath)],
                check=True, capture_output=True
            )
            subprocess.run(
                ["git", "-C", str(self.website_repo), "commit",
                 "-m", f"Changelog: {today.strftime('%B %d')} updates"],
                check=True, capture_output=True
            )
            logger.info("Committed changelog to website repo")

            # Update state
            now = datetime.now(timezone.utc).isoformat()
            self._save_state(now, str(today))

            return True

        except Exception as e:
            logger.error(f"Failed to update changelog: {e}")
            return False


# Global instance
_updater: Optional[ChangelogUpdater] = None


def get_changelog_updater() -> ChangelogUpdater:
    """Get or create global changelog updater."""
    global _updater
    if _updater is None:
        from .config import ConfigHandler
        config = ConfigHandler()
        projects = {
            "Local Manager": config.resolve_path("PROJECT_LOCAL_MANAGER_REPO_PATH", "."),
            "Snipzilla": config.resolve_path("PROJECT_SNIPZILLA_REPO_PATH", ""),
            "Website": config.resolve_path("PROJECT_WEBSITE_REPO_PATH", ""),
            "Stashzilla": config.resolve_path("PROJECT_STASHZILLA_REPO_PATH", ""),
        }
        website_repo = config.resolve_path("PROJECT_WEBSITE_REPO_PATH", "")
        _updater = ChangelogUpdater(projects, website_repo)
    return _updater
