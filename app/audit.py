"""JSON audit-trail writer (§5.2).

Each session has one audit file at:  {base}/{project}/{session_id}.json
Writes are atomic (.tmp + os.replace). New apps (no project yet) are filed
under '_new_app'. The schema mirrors the architecture doc; this module just
owns durable read/write/merge — the orchestrator fills the content.
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

_UNASSIGNED = "_new_app"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditTrail:
    """Durable JSON audit log, one file per session."""

    def __init__(self, base_path: os.PathLike | str):
        self.base_path = Path(base_path)
        self._lock = threading.RLock()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str, project: Optional[str]) -> Path:
        folder = self.base_path / (project or _UNASSIGNED)
        return folder / f"{session_id}.json"

    def write(self, session_id: str, project: Optional[str],
              data: dict[str, Any]) -> Path:
        """Write (overwrite) the full audit record atomically."""
        path = self.path_for(session_id, project)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        logger.debug("Wrote audit log %s", path)
        return path

    def read(self, session_id: str,
             project: Optional[str]) -> Optional[dict[str, Any]]:
        path = self.path_for(session_id, project)
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def update(self, session_id: str, project: Optional[str],
               patch: dict[str, Any]) -> Path:
        """Shallow-merge `patch` into the existing record (create if absent)."""
        with self._lock:
            current = self.read(session_id, project) or {
                "session_id": session_id,
                "project": project,
                "created_at": _utc_now(),
            }
            current.update(patch)
            return self.write(session_id, project, current)

    def append_cycle(self, session_id: str, project: Optional[str],
                     cycle: dict[str, Any]) -> Path:
        """Append a cycle entry (Opus→Sonnet test round) to the record."""
        with self._lock:
            current = self.read(session_id, project) or {
                "session_id": session_id,
                "project": project,
                "created_at": _utc_now(),
                "cycles": [],
            }
            current.setdefault("cycles", []).append(cycle)
            return self.write(session_id, project, current)
