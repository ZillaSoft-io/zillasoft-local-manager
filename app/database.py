"""SQLite persistence for the Local Manager — sessions table (§5.1).

The audit trail itself is stored as JSON files (see audit.py); this module
owns the relational `sessions` table used for listing, status, and cost
roll-ups in the UI.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

# Verbatim from LOCAL_MANAGER_ARCHITECTURE.md §5.1, with IF NOT EXISTS so init
# is idempotent. JSON-typed columns are stored as TEXT and (de)serialized here.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status TEXT,
  task_type TEXT,
  project TEXT,
  scope_level TEXT,
  input_source TEXT,
  input_ref TEXT,
  mario_approved BOOLEAN DEFAULT FALSE,
  mario_approved_at TIMESTAMP,
  cycle_count INTEGER DEFAULT 0,
  total_tokens_used INTEGER DEFAULT 0,
  total_cost REAL DEFAULT 0.0,
  cost_breakdown TEXT,
  haiku_context TEXT,
  sonnet_instructions TEXT,
  opus_changes TEXT,
  sonnet_review TEXT,
  deployment_status TEXT,
  setup_log TEXT,
  error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_status  ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
  content TEXT NOT NULL DEFAULT '',
  attachments TEXT,            -- JSON list of attachment refs
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""

_MESSAGE_ROLES = frozenset({"user", "assistant", "system"})

# Columns that hold JSON and should be (de)serialized transparently.
_JSON_COLUMNS = frozenset({
    "cost_breakdown", "haiku_context", "sonnet_instructions",
    "opus_changes", "sonnet_review", "deployment_status",
})

_VALID_STATUSES = frozenset({
    "in_progress", "awaiting_approval", "approved", "deployed",
    "failed", "rolled_back", "stopped", "paused",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin, thread-safe wrapper around the SQLite sessions store."""

    def __init__(self, db_path: os.PathLike | str):  # type: ignore[name-defined]
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info("SQLite schema ready at %s", self.db_path)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def create_session(self, **fields: Any) -> str:
        """Insert a new session row. Returns the session id (UUID)."""
        session_id = fields.pop("id", None) or str(uuid.uuid4())
        status = fields.setdefault("status", "in_progress")
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}")
        now = _utc_now()
        row = {"id": session_id, "created_at": now, "updated_at": now, **fields}
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        values = [self._encode(k, v) for k, v in row.items()]
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO sessions ({cols}) VALUES ({placeholders})", values)
        logger.info("Created session %s (%s)", session_id, status)
        return session_id

    def update_session(self, session_id: str, **fields: Any) -> None:
        """Update columns on a session and bump updated_at."""
        if "status" in fields and fields["status"] not in _VALID_STATUSES:
            raise ValueError(f"Invalid status {fields['status']!r}")
        fields["updated_at"] = _utc_now()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = [self._encode(k, v) for k, v in fields.items()]
        values.append(session_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?", values)
            if cur.rowcount == 0:
                raise KeyError(f"No session with id {session_id}")

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
        return self._decode_row(row) if row else None

    def list_sessions(self, limit: int = 20,
                      project: Optional[str] = None) -> list[dict[str, Any]]:
        """Most-recent-first session list for the history sidebar (§11.2)."""
        query = "SELECT * FROM sessions"
        params: list[Any] = []
        if project:
            query += " WHERE project = ?"
            params.append(project)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Messages (chatbox transcript)
    # ------------------------------------------------------------------ #
    def add_message(self, session_id: str, role: str, content: str = "",
                    attachments: Optional[list] = None) -> int:
        if role not in _MESSAGE_ROLES:
            raise ValueError(f"Invalid message role {role!r}")
        att = json.dumps(attachments) if attachments else None
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, attachments) "
                "VALUES (?, ?, ?, ?)", (session_id, role, content, att))
            return int(cur.lastrowid)

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            if d.get("attachments"):
                try:
                    d["attachments"] = json.loads(d["attachments"])
                except json.JSONDecodeError:
                    d["attachments"] = []
            else:
                d["attachments"] = []
            out.append(d)
        return out

    # ------------------------------------------------------------------ #
    # JSON (de)serialization for JSON-typed columns
    # ------------------------------------------------------------------ #
    @staticmethod
    def _encode(key: str, value: Any) -> Any:
        if key in _JSON_COLUMNS and value is not None and not isinstance(value, str):
            return json.dumps(value)
        return value

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for col in _JSON_COLUMNS:
            raw = out.get(col)
            if isinstance(raw, str) and raw:
                try:
                    out[col] = json.loads(raw)
                except json.JSONDecodeError:
                    pass  # leave as string if it isn't valid JSON
        return out
