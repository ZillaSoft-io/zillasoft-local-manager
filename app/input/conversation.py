"""ConversationManager — drives Haiku's clarification chat (spec §3.1, §6).

Per message it:
  1. Detects Sentry/Jira references in Mario's text and fetches+stores their
     summaries (when those integrations are configured).
  2. Persists the user turn (with any image attachments).
  3. Replays the transcript to Haiku, which either asks another question or
     signals "ready" with a compiled context summary + scope.
  4. On ready, finalizes the session (context, scope, cost cap, audit input).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..agents.haiku import ClarifyTurn, HaikuAgent
from ..integrations import JiraClient, JiraError, SentryClient, SentryError
from .attachments import AttachmentStore
from .task_types import TASK_TYPES, clarify_instructions

logger = logging.getLogger(__name__)

_SENTRY_PROJECT_KEYS = {
    "website": "SENTRY_PROJECT_WEBSITE",
    "snipzilla": "SENTRY_PROJECT_SNIPZILLA",
    "stashzilla": "SENTRY_PROJECT_STASHZILLA",
}


class ConversationManager:
    def __init__(self, config, db, audit, haiku: HaikuAgent, *,
                 sentry: Optional[SentryClient] = None,
                 jira: Optional[JiraClient] = None,
                 attachments: Optional[AttachmentStore] = None,
                 uploads_dir=None):
        self.config = config
        self.db = db
        self.audit = audit
        self.haiku = haiku
        self.sentry = sentry or SentryClient(config)
        self.jira = jira or JiraClient(config)
        self.attachments = attachments or AttachmentStore(
            uploads_dir or (config.root / "uploads"))

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def create_session(self, task_type: str,
                       project: Optional[str] = None) -> str:
        # "auto" lets Haiku detect bug_fix vs feature from the description.
        if task_type not in TASK_TYPES and task_type != "auto":
            raise ValueError(f"Invalid task_type {task_type!r}")
        return self.db.create_session(
            task_type=task_type, project=project, input_source="manual")

    def handle_message(self, session_id: str, text: str,
                       attachment_refs: Optional[list] = None) -> ClarifyTurn:
        session = self.db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")

        self._maybe_fetch_external(session, text)
        self.db.add_message(session_id, "user", text, attachments=attachment_refs)

        api_messages = self._build_api_messages(session_id)
        instr = clarify_instructions(
            session["task_type"],
            external_context=self._external_text(session_id),
        )
        turn = self.haiku.clarify(clarify_instructions=instr,
                                  messages=api_messages)
        self.db.add_message(session_id, "assistant", turn.message)

        # Auto-detected task type: persist it as Haiku firms it up, so later
        # turns use the right checklist and the pipeline sees the real type.
        if turn.task_type in TASK_TYPES and turn.task_type != session.get("task_type"):
            self.db.update_session(session_id, task_type=turn.task_type)
            session["task_type"] = turn.task_type

        if turn.status == "ready":
            self._finalize(session, turn)
        return turn

    def transcript(self, session_id: str) -> list[dict[str, Any]]:
        return self.db.list_messages(session_id)

    # ------------------------------------------------------------------ #
    # External context (Sentry / Jira)
    # ------------------------------------------------------------------ #
    def _maybe_fetch_external(self, session: dict, text: str) -> None:
        sid = session["id"]
        seen = session.get("input_ref") or ""

        sref = SentryClient.extract_event_id(text)
        if sref and self.sentry.configured() and f"sentry:{sref}" not in seen:
            try:
                summary = self.sentry.fetch_event(
                    sref, project=self._sentry_project(session))
                self._store_external(session, f"sentry:{sref}", "sentry", summary)
            except SentryError as exc:
                self.db.add_message(sid, "system", f"[Sentry fetch failed: {exc}]")

        jkey = JiraClient.extract_key(text)
        if jkey and self.jira.configured() and f"jira:{jkey}" not in seen:
            try:
                summary = self.jira.fetch_issue(jkey)
                self._store_external(session, f"jira:{jkey}", "jira", summary)
            except JiraError as exc:
                self.db.add_message(sid, "system", f"[Jira fetch failed: {exc}]")

    def _store_external(self, session: dict, marker: str, source: str,
                        summary: dict) -> None:
        sid = session["id"]
        lines = [f"[EXTERNAL CONTEXT — {source.upper()}]"]
        for k, v in summary.items():
            if v:
                lines.append(f"{k}: {v}")
        self.db.add_message(sid, "system", "\n".join(lines))
        existing = session.get("input_ref") or ""
        new_ref = (existing + " " + marker).strip()
        self.db.update_session(sid, input_source=source, input_ref=new_ref)
        session["input_ref"] = new_ref  # keep local copy fresh for dedupe

    def _sentry_project(self, session: dict) -> Optional[str]:
        key = _SENTRY_PROJECT_KEYS.get(session.get("project") or "")
        return self.config.get_raw(key) if key else None

    def _external_text(self, session_id: str) -> str:
        chunks = [m["content"] for m in self.db.list_messages(session_id)
                  if m["role"] == "system" and m["content"].startswith(
                      "[EXTERNAL CONTEXT")]
        return "\n\n".join(chunks)

    # ------------------------------------------------------------------ #
    # Message building
    # ------------------------------------------------------------------ #
    def _build_api_messages(self, session_id: str) -> list[dict]:
        """Build the Anthropic messages list from the transcript.

        System messages (external context) are excluded — they're fed via the
        system prompt addendum. Image attachments become vision content blocks.
        """
        out: list[dict] = []
        for m in self.db.list_messages(session_id):
            if m["role"] == "system":
                continue
            content = m["content"]
            blocks = []
            for ref in m.get("attachments") or []:
                blk = AttachmentStore.to_content_block(ref)
                if blk:
                    blocks.append(blk)
            if blocks:
                parts = ([{"type": "text", "text": content}] if content else [])
                out.append({"role": m["role"], "content": parts + blocks})
            else:
                out.append({"role": m["role"], "content": content})
        return out

    # ------------------------------------------------------------------ #
    # Finalize on ready
    # ------------------------------------------------------------------ #
    def _finalize(self, session: dict, turn: ClarifyTurn) -> None:
        sid = session["id"]
        # Make sure an auto session ends with a concrete task type (default to
        # feature if Haiku somehow never resolved it).
        if session.get("task_type") not in TASK_TYPES:
            resolved = turn.task_type if turn.task_type in TASK_TYPES else "feature"
            self.db.update_session(sid, task_type=resolved)
            session["task_type"] = resolved
        self.db.update_session(
            sid,
            scope_level=turn.scope_level or None,
            haiku_context={
                "summary": turn.context_summary,
                "recommended_stack": turn.recommended_stack,
                "app_name": turn.app_name,
                "scope_level": turn.scope_level,
                "monthly_cap": turn.monthly_cap,
            },
        )
        if turn.scope_level == "capped" and turn.monthly_cap > 0:
            # Project/manager setting — agent-writable, not a credential.
            self.config.set("LOCAL_MANAGER_MONTHLY_COST_CAP",
                            turn.monthly_cap, actor="agent")

        self.audit.update(sid, session.get("project"), {
            "task_type": session.get("task_type"),
            "project": session.get("project"),
            "scope_level": turn.scope_level,
            "monthly_cost_cap": turn.monthly_cap,
            "input": {
                "source": session.get("input_source"),
                "input_ref": session.get("input_ref"),
            },
            "context": {"haiku_summary": turn.context_summary},
            "recommended_stack": turn.recommended_stack,
        })
        logger.info("Session %s input complete (scope=%s).",
                    sid, turn.scope_level)
