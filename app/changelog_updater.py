"""Daily changelog aggregator for all ZillaSoft projects.

Scans git commits from all projects (Local Manager, Snipzilla, Website, Stashzilla),
summarizes with Haiku, and posts daily entries to the website changelog.
Prevents spam by batching all daily changes into one entry.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ChangelogUpdater:
    """Aggregates commits and posts daily to website changelog."""

    def __init__(self, projects: dict[str, str], website_repo: str, haiku_agent=None):
        """
        Args:
            projects: dict of {project_name: repo_path} (only public-facing: Snipzilla, Website, Stashzilla)
            website_repo: path to zillasoft.io website repo
            haiku_agent: Haiku agent for summarization (optional)
        """
        self.projects = {k: v for k, v in projects.items() if k != "Local Manager"}
        self.website_repo = Path(website_repo)
        self.haiku = haiku_agent
        self.state_file = Path(".last_changelog_update.json")
        self.changelog_file = self.website_repo / "src" / "pages" / "changelog.astro"

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

    def _write_commits_to_temp_file(self, commits: list[dict]) -> Path:
        """Write commits to a temp file for Haiku to read."""
        commit_lines = []
        for c in commits[:10]:  # Limit to 10 commits
            commit_lines.append(f"- {c['subject']}")
            if c.get('body'):
                # Add first line of body if available
                body_first = c['body'].split('\n')[0].strip()
                if body_first:
                    commit_lines.append(f"  ({body_first})")

        # Create temp file
        temp_file = Path(tempfile.gettempdir()) / f"zlm_changelog_{datetime.now().timestamp()}.txt"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(commit_lines))

        logger.debug(f"Wrote commits to temp file: {temp_file}")
        return temp_file

    def _summarize_with_haiku(self, commits: list[dict]) -> Optional[str]:
        """Use Haiku to read commits from temp file and summarize."""
        if not self.haiku or not commits:
            return None

        temp_file = None
        try:
            # Write commits to temp file
            temp_file = self._write_commits_to_temp_file(commits)

            # Read file contents
            with open(temp_file, "r", encoding="utf-8") as f:
                commit_text = f.read()

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

        finally:
            # Clean up temp file
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                    logger.debug(f"Deleted temp file: {temp_file}")
                except Exception as e:
                    logger.warning(f"Failed to delete temp file: {e}")

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

    def _read_changelog_file(self) -> tuple[str, str, str]:
        """Read changelog.astro file, return (before_entries, entries_array, after_entries)."""
        with open(self.changelog_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the const entries array
        start_marker = "const entries: ChangelogEntry[] = ["
        end_marker = "];"

        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker, start_idx)

        if start_idx == -1 or end_idx == -1:
            raise ValueError("Could not find entries array in changelog.astro")

        before = content[:start_idx + len(start_marker)]
        entries_text = content[start_idx + len(start_marker):end_idx]
        after = content[end_idx:]

        return before, entries_text, after

    def _generate_astro_entry(self, project_commits: dict[str, list[dict]]) -> str:
        """Generate an Astro changelog entry object."""
        from datetime import datetime
        today = datetime.now(timezone.utc).date()
        categories = self._categorize_commits(self._flatten_commits(project_commits))

        # Build arrays for added, changed, fixed
        added_items = []
        if categories["announcements"]:
            summary = self._summarize_with_haiku(categories["announcements"])
            if summary:
                added_items.append(f'      "{summary}",')
        if categories["features"]:
            summary = self._summarize_with_haiku(categories["features"])
            if summary:
                added_items.append(f'      "{summary}",')

        changed_items = []
        if categories["improvements"]:
            summary = self._summarize_with_haiku(categories["improvements"])
            if summary:
                changed_items.append(f'      "{summary}",')

        fixed_items = []
        if categories["bugs"]:
            summary = self._summarize_with_haiku(categories["bugs"])
            if summary:
                fixed_items.append(f'      "{summary}",')

        # Generate entry object
        entry = f"""  {{
    version: "auto-{today.strftime('%Y%m%d')}",
    date: "{today.strftime('%B %d, %Y')}",
    product: "ZillaSoft",
    title: "{today.strftime('%B %d')} Updates","""

        if added_items:
            entry += "\n    added: [\n" + "\n".join(added_items) + "\n    ],"
        if changed_items:
            entry += "\n    changed: [\n" + "\n".join(changed_items) + "\n    ],"
        if fixed_items:
            entry += "\n    fixed: [\n" + "\n".join(fixed_items) + "\n    ],"

        entry += "\n  },"
        return entry

    def _flatten_commits(self, project_commits: dict[str, list[dict]]) -> list[dict]:
        """Flatten commits from all projects into one list."""
        all_commits = []
        for commits in project_commits.values():
            all_commits.extend(commits)
        return all_commits

    def _find_today_entry(self, entries_text: str) -> tuple[int, int] | None:
        """Find the start and end indices of today's entry in the entries array.

        Returns (start_idx, end_idx) or None if not found.
        """
        from datetime import datetime
        today = datetime.now(timezone.utc).date()
        search_str = f'date: "{today.strftime("%B %d, %Y")}"'

        idx = entries_text.find(search_str)
        if idx == -1:
            return None

        # Find the start of this entry (opening brace)
        start_idx = entries_text.rfind("{", 0, idx)
        if start_idx == -1:
            return None

        # Find the end of this entry (closing brace and comma)
        end_idx = entries_text.find("},", idx)
        if end_idx == -1:
            return None

        end_idx += 2  # Include the "},"

        return (start_idx, end_idx)

    def update_changelog(self) -> bool:
        """Scan all projects, summarize, and add/update website changelog.astro.

        If an entry for today already exists, it's replaced. Otherwise a new one is added.
        """
        if not self.should_update():
            logger.info("Changelog updated recently, skipping")
            return False

        logger.info("Starting daily changelog update...")

        # Get state
        state = self._load_state()
        last_update = state.get("last_update")

        # Collect commits from all projects (excluding Local Manager)
        project_commits = {}
        for project_name, repo_path in self.projects.items():
            commits = self._get_commits_since(repo_path, last_update)
            if commits:
                project_commits[project_name] = commits
                logger.info(f"{project_name}: {len(commits)} commits since last update")

        if not project_commits:
            logger.info("No new commits found")
            return False

        try:
            # Generate entry
            entry_obj = self._generate_astro_entry(project_commits)
            if not entry_obj:
                logger.warning("Failed to generate changelog entry")
                return False

            # Read current changelog
            before, entries_text, after = self._read_changelog_file()

            # Check if today's entry already exists
            today_entry = self._find_today_entry(entries_text)

            if today_entry:
                # Replace existing entry
                start_idx, end_idx = today_entry
                new_entries = entries_text[:start_idx] + entry_obj + "\n" + entries_text[end_idx:]
                logger.info(f"Replacing existing entry for today")
            else:
                # Insert new entry at the top
                new_entries = entry_obj + "\n" + entries_text
                logger.info(f"Adding new entry for today")

            # Write updated file
            new_content = before + new_entries + after
            with open(self.changelog_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info(f"Updated changelog.astro")

            # Commit to website repo
            subprocess.run(
                ["git", "-C", str(self.website_repo), "add", str(self.changelog_file)],
                check=True, capture_output=True
            )
            today = datetime.now(timezone.utc).date()
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
