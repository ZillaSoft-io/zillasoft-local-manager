"""Task-type definitions and the per-type clarification guidance (spec §6, §9).

The guidance text is injected into Haiku's system prompt for the input phase so
its questions cover the right fields for bug fixes, features, and new apps, and
so it always collects the scope (Capped vs Uncapped) before signalling ready.
"""
from __future__ import annotations

TASK_TYPES = ("bug_fix", "feature", "new_app")
PROJECTS = ("website", "snipzilla", "stashzilla")  # null for new apps
SCOPE_LEVELS = ("capped", "uncapped")

_BUG_FIELDS = """\
For a BUG FIX, make sure you learn:
- Which project (website / snipzilla / stashzilla).
- Where the bug is from (manual description / Sentry event / Jira ticket). If
  Mario pasted a Sentry URL or Jira key, the fetched details are provided below.
- What breaks, expected behavior, and how to reproduce.
- Which files/areas are likely affected.
- Priority (optional)."""

_FEATURE_FIELDS = """\
For a FEATURE, make sure you learn:
- Which project (website / snipzilla / stashzilla).
- Detailed requirements (UI location, persistence, supported browsers, etc.).
- Which files/areas this will touch.
- Any design specs or reference implementations.
- Who the users are, and whether it must be backward-compatible.
- Any new dependencies needed."""

_NEW_APP_FIELDS = """\
For a NEW APP, make sure you learn:
- Purpose and target audience.
- Estimated user base and scaling expectations.
- Real-time or async workflows.
- Integrations with existing systems (Auth0, Stripe, Sentry, Railway, etc.).
Then RECOMMEND a tech stack in `recommended_stack` based on context:
- Python + FastAPI for data-heavy / real-time / complex logic (like Snipzilla,
  Stashzilla) or desktop/mobile client backends.
- Astro + TypeScript for marketing/static/SEO sites (like zillasoft.io).
Mario can override your recommendation."""

_FIELDS = {
    "bug_fix": _BUG_FIELDS,
    "feature": _FEATURE_FIELDS,
    "new_app": _NEW_APP_FIELDS,
}

_BASE = """\
You are gathering requirements for a {task_type} task. Ask ONE focused question
at a time. Always ask, never assume. Respond ONLY as the structured object.

While you still need information, set status="asking" and put your next question
in `message`. Before you finish, you MUST also have asked Mario for the scope:
Capped (set a monthly API cost limit in dollars) or Uncapped (no limit). If
Capped, capture the dollar amount in `monthly_cap`.

When you have everything (including scope), set status="ready", put a brief
confirmation in `message`, and write a concise context summary for Sonnet in
`context_summary` (key outputs only, no filler). Set `scope_level` to "capped"
or "uncapped" and `monthly_cap` accordingly (0 if uncapped).

{fields}"""


def clarify_instructions(task_type: str,
                        external_context: str = "") -> str:
    """Build the system-prompt addendum for one clarification session."""
    fields = _FIELDS.get(task_type, _FEATURE_FIELDS)
    text = _BASE.format(task_type=task_type, fields=fields)
    if external_context:
        text += ("\n\nFetched context from Sentry/Jira (use this, don't ask "
                 "Mario to repeat it):\n" + external_context)
    return text
