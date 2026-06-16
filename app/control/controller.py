"""SessionController — kill switch and pause/resume (spec §11.3, §11.4).

Kill and pause are *cooperative*: they set a per-session signal the orchestrator
(Phase 5) polls between steps, and they immediately transition session status
and persist state so they work and are observable now.

- Kill   -> status 'stopped'; (commit of in-flight work is wired in Phase 6).
- Pause  -> serialize the agent context snapshot to paused/{id}.json; status
            'paused'. Resume restores it. Snapshots expire after N days.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _Signals:
    __slots__ = ("stop", "pause")

    def __init__(self):
        self.stop = threading.Event()
        self.pause = threading.Event()


class SessionController:
    def __init__(self, config, db, audit, *, pause_dir: os.PathLike | str,
                 notifier=None):
        self._config = config
        self._db = db
        self._audit = audit
        self._notifier = notifier
        self._pause_dir = Path(pause_dir)
        self._pause_dir.mkdir(parents=True, exist_ok=True)
        self._signals: dict[str, _Signals] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Cooperative signals (polled by the orchestrator)
    # ------------------------------------------------------------------ #
    def _sig(self, session_id: str) -> _Signals:
        with self._lock:
            return self._signals.setdefault(session_id, _Signals())

    def request_stop(self, session_id: str) -> None:
        self._sig(session_id).stop.set()

    def request_pause(self, session_id: str) -> None:
        self._sig(session_id).pause.set()

    def should_stop(self, session_id: str) -> bool:
        return self._sig(session_id).stop.is_set()

    def should_pause(self, session_id: str) -> bool:
        return self._sig(session_id).pause.is_set()

    def clear(self, session_id: str) -> None:
        s = self._sig(session_id)
        s.stop.clear()
        s.pause.clear()

    @property
    def expiry_days(self) -> int:
        return int(self._config.get("LOCAL_MANAGER_PAUSE_EXPIRY_DAYS", 7) or 7)

    # ------------------------------------------------------------------ #
    # Kill switch
    # ------------------------------------------------------------------ #
    def kill(self, session_id: str, reason: str = "",
             commit_sha: Optional[str] = None) -> dict:
        session = self._db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")
        self.request_stop(session_id)
        self._db.update_session(session_id, status="stopped",
                                error_message=reason or None)
        self._audit.update(session_id, session.get("project"), {
            "stopped": {
                "at": _utc_now().isoformat(),
                "reason": reason,
                "commit_sha": commit_sha,
            }})
        if self._notifier:
            self._notifier.notify(
                "failure",
                title="Session stopped",
                message=f"Session {session_id[:8]} stopped (kill switch).",
                email_subject="ZillaSoft Local Manager — session stopped")
        logger.info("Session %s killed: %s", session_id, reason)
        return self._db.get_session(session_id)

    # ------------------------------------------------------------------ #
    # Pause / resume
    # ------------------------------------------------------------------ #
    def _pause_path(self, session_id: str) -> Path:
        return self._pause_dir / f"{session_id}.json"

    def save_pause(self, session_id: str,
                   snapshot: Optional[dict] = None) -> Path:
        """Serialize the agent context snapshot and mark the session paused.

        `snapshot` carries the orchestrator's in-flight state (cycle number,
        last instructions, uncommitted changes, test output, cost so far).
        """
        session = self._db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")
        self.request_pause(session_id)
        record = {
            "session_id": session_id,
            "project": session.get("project"),
            "paused_at": _utc_now().isoformat(),
            "snapshot": snapshot or {},
        }
        path = self._pause_path(session_id)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
        self._db.update_session(session_id, status="paused")
        if self._notifier:
            self._notifier.notify(
                "paused",
                title="Session paused",
                message=f"Session {session_id[:8]} paused; resume anytime.")
        logger.info("Session %s paused.", session_id)
        return path

    def resume(self, session_id: str) -> dict:
        """Restore a paused session's snapshot and mark it in-progress."""
        path = self._pause_path(session_id)
        if not path.exists():
            raise KeyError(f"No paused snapshot for {session_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        age_days = self._age_days(record.get("paused_at"))
        if age_days is not None and age_days >= self.expiry_days:
            self._expire(session_id, path)
            raise KeyError(f"Paused session {session_id} expired "
                           f"({age_days:.1f}d > {self.expiry_days}d).")
        self.clear(session_id)
        self._db.update_session(session_id, status="in_progress")
        logger.info("Session %s resumed.", session_id)
        return record

    def list_resumable(self) -> list[dict]:
        out = []
        for path in sorted(self._pause_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            age = self._age_days(record.get("paused_at"))
            if age is not None and age >= self.expiry_days:
                continue
            out.append({
                "session_id": record.get("session_id"),
                "project": record.get("project"),
                "paused_at": record.get("paused_at"),
                "age_days": round(age, 2) if age is not None else None,
            })
        return out

    def sweep_expired(self) -> int:
        """Delete expired pause snapshots and mark their sessions failed.
        Called on startup so it self-heals even after long idle periods."""
        swept = 0
        for path in list(self._pause_dir.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            age = self._age_days(record.get("paused_at"))
            if age is not None and age >= self.expiry_days:
                self._expire(record.get("session_id"), path)
                swept += 1
        if swept:
            logger.info("Swept %d expired paused session(s).", swept)
        return swept

    # ------------------------------------------------------------------ #
    def _expire(self, session_id: Optional[str], path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if session_id and self._db.get_session(session_id):
            self._db.update_session(
                session_id, status="failed",
                error_message="paused session expired")

    @staticmethod
    def _age_days(paused_at: Optional[str]) -> Optional[float]:
        if not paused_at:
            return None
        try:
            then = datetime.fromisoformat(paused_at)
        except ValueError:
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (_utc_now() - then).total_seconds() / 86400.0
