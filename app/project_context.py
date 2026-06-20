"""Load a project's conventions/tech-stack (its CLAUDE.md) for the agent context.

The implementer operates inside the target repo and *could* read CLAUDE.md
itself, but that's "if it remembers to". Injecting it up front guarantees the
project's conventions (no em dashes, pnpm over npm, i18n layout, etc.) are always
present — and because it's stable per project, prompt caching makes it ~0.1x on
repeats across the tool loop.

Never raises: any problem returns "" and the agent falls back to reading files
at runtime as before.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 16000  # ~4k tokens; conventions live near the top


def load_project_conventions(repo_path: str | None,
                             max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Return the project's conventions for `repo_path`, capped and wrapped.

    Collects up to two CLAUDE.md files walking up from the repo — the
    repo-specific one (closest, most relevant) AND the workspace-level one (which
    holds the global conventions and cross-project tech stack) — so neither the
    repo specifics nor the global rules are missed. Returns "" if none found.
    """
    if not repo_path:
        return ""
    try:
        start = Path(repo_path).resolve()
        found: list[tuple[Path, str]] = []
        for base in [start, *start.parents][:5]:
            candidate = base / "CLAUDE.md"
            if candidate.is_file():
                found.append(
                    (base, candidate.read_text(encoding="utf-8", errors="replace")))
                if len(found) >= 2:   # repo-specific + workspace is enough
                    break
        if not found:
            return ""
        combined = "\n\n".join(
            f"--- conventions from {base}/CLAUDE.md ---\n{text}"
            for base, text in found)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + "\n…[truncated]"
        return ("PROJECT CONVENTIONS & TECH STACK (follow these exactly):\n\n"
                + combined)
    except Exception as e:
        logger.warning("Could not load project conventions for %s: %s",
                       repo_path, e)
    return ""
