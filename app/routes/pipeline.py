"""Pipeline run API (Phase 5).

  POST /api/sessions/{id}/run   start the orchestrator for a ready session

The pipeline is long-running, so it runs in a background daemon thread; the
kill/pause endpoints signal it cooperatively via the controller.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

from .. import main as _main  # noqa: E402

router = APIRouter(prefix="/api", tags=["pipeline"],
                   dependencies=[Depends(_main.require_auth)])

_threads: dict[str, threading.Thread] = {}


def _safe_run(session_id: str) -> None:
    try:
        _main.state.orchestrator.run_session(session_id)
    except Exception as exc:  # never let a worker thread die silently
        logger.exception("Pipeline crashed for %s", session_id)
        try:
            _main.state.db.update_session(session_id, status="failed",
                                          error_message=f"pipeline error: {exc}")
        except Exception:
            pass


@router.post("/sessions/{session_id}/run")
async def run_pipeline(session_id: str):
    session = _main.state.db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not (session.get("haiku_context") or {}).get("summary"):
        raise HTTPException(status_code=409,
                            detail="Input not complete — run the chatbox first.")
    existing = _threads.get(session_id)
    if existing and existing.is_alive():
        raise HTTPException(status_code=409, detail="Pipeline already running.")
    t = threading.Thread(target=_safe_run, args=(session_id,), daemon=True)
    _threads[session_id] = t
    t.start()
    return {"started": True, "session_id": session_id}
