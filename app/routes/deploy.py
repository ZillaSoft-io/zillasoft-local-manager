"""Deployment tracking API (Phase 8).

  POST /api/sessions/{id}/deploy   start deployment monitoring (background)

Approval auto-starts this; the endpoint lets Mario (re)trigger it manually.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

from .. import main as _main  # noqa: E402

router = APIRouter(prefix="/api", tags=["deploy"],
                   dependencies=[Depends(_main.require_auth)])

_threads: dict[str, threading.Thread] = {}


def _safe_track(session_id: str) -> None:
    try:
        _main.state.deploy_tracker.track(session_id)
    except Exception as exc:
        logger.exception("Deploy tracking crashed for %s", session_id)
        try:
            _main.state.db.update_session(
                session_id, status="failed",
                error_message=f"deploy tracking error: {exc}")
        except Exception:
            pass


def start_deploy(session_id: str) -> bool:
    """Launch deployment tracking in a background thread (idempotent)."""
    existing = _threads.get(session_id)
    if existing and existing.is_alive():
        return False
    t = threading.Thread(target=_safe_track, args=(session_id,), daemon=True)
    _threads[session_id] = t
    t.start()
    return True


@router.post("/sessions/{session_id}/deploy")
async def deploy(session_id: str):
    if _main.state.db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    started = start_deploy(session_id)
    return {"started": started, "session_id": session_id}
