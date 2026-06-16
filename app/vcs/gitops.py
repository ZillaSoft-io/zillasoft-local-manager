"""Git operations over a repo, driven by the CodeExecutor (spec Phase 6).

Local-only by default: Opus commits during the cycle loop; pushing happens only
on Mario's approval. Push uses an explicit authenticated URL (built from
GITHUB_TOKEN) so it works even for repos without an `origin` remote (e.g. the
local Snipzilla clone).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

_TOKEN_IN_URL = re.compile(r"x-access-token:[^@]+@")


class GitError(Exception):
    pass


def _redact(text: str) -> str:
    return _TOKEN_IN_URL.sub("x-access-token:***@", text or "")


class GitOps:
    def __init__(self, repo_path: str, executor):
        self.repo_path = str(repo_path)
        self._ex = executor

    def _run(self, command: str):
        return self._ex.run(command, cwd=self.repo_path)

    # ------------------------------------------------------------------ #
    def head_sha(self) -> Optional[str]:
        r = self._run("git rev-parse HEAD")
        return r.stdout.strip() if r.ok else None

    def current_branch(self) -> Optional[str]:
        r = self._run("git rev-parse --abbrev-ref HEAD")
        return r.stdout.strip() if r.ok else None

    def is_clean(self) -> bool:
        return self._run("git status --porcelain").stdout.strip() == ""

    def has_remote(self, name: str = "origin") -> bool:
        return self._run(f"git remote get-url {name}").ok

    def diff_stat(self, rev_range: str = "HEAD~1..HEAD") -> str:
        r = self._run(f"git diff --stat {rev_range}")
        return r.stdout.strip()

    # ------------------------------------------------------------------ #
    def commit(self, message: str) -> Optional[str]:
        """Stage all changes and commit. Returns the new SHA, or the current
        HEAD if there was nothing to commit."""
        self._run("git add -A")
        fd, path = tempfile.mkstemp(suffix=".txt", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(message)
            posix = path.replace("\\", "/")
            r = self._run(f'git commit -F "{posix}"')
        finally:
            os.unlink(path)
        if not r.ok and "nothing to commit" not in (r.stdout + r.stderr).lower():
            raise GitError(f"commit failed: {_redact(r.stderr)}")
        return self.head_sha()

    def push(self, branch: Optional[str] = None, *, url: Optional[str] = None,
             remote: str = "origin") -> None:
        branch = branch or self.current_branch()
        target = f'"{url}"' if url else remote
        r = self._run(f"git push {target} HEAD:{branch}")
        if not r.ok:
            raise GitError(f"push failed: {_redact(r.stderr)}")

    def revert(self, sha: str) -> Optional[str]:
        r = self._run(f"git revert --no-edit {sha}")
        if not r.ok:
            raise GitError(f"revert failed: {_redact(r.stderr)}")
        return self.head_sha()

    def reset_hard(self, sha: str) -> None:
        r = self._run(f"git reset --hard {sha}")
        if not r.ok:
            raise GitError(f"reset failed: {_redact(r.stderr)}")

    def clone(self, url: str, dest: str) -> None:
        from pathlib import Path
        parent = str(Path(dest).parent)
        name = Path(dest).name
        r = self._ex.run(f'git clone "{url}" "{name}"', cwd=parent)
        if not r.ok:
            raise GitError(f"clone failed: {_redact(r.stderr)}")
