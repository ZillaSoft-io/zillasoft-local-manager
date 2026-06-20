"""Pipeline run API (Phase 5).

  POST /api/sessions/{id}/run   start the orchestrator for a ready session

The pipeline is long-running, so it runs in a background daemon thread; the
kill/pause endpoints signal it cooperatively via the controller.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from ..batch_executor import get_batch_executor, BatchTask

router = APIRouter(prefix="/api", tags=["pipeline"])

# Imported at the bottom of main.py (after `state`); `_main.state` is
# only read at request time, so this is not a circular import.
from .. import main as _main  # noqa: E402

_threads: dict[str, threading.Thread] = {}


# ============================================================================
# Request/Response models
# ============================================================================

class BatchRunRequest(BaseModel):
    """Request to run multiple sessions in parallel."""
    session_ids: list[str]
    max_parallel: int = 4


# ============================================================================
# Pipeline endpoints
# ============================================================================

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


# ============================================================================
# Batch execution (Improvement 4)
# ============================================================================

@router.post("/sessions/batch/run")
async def run_batch_sessions(req: BatchRunRequest) -> dict[str, Any]:
    """Run multiple sessions in parallel.

    Improvement 4: Batch executor runs up to max_parallel sessions
    concurrently, saving 3-4x wall-clock time for bulk work.
    """
    # Validate all sessions exist
    for session_id in req.session_ids:
        session = _main.state.db.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"Session {session_id} not found."
            )
        if not (session.get("haiku_context") or {}).get("summary"):
            raise HTTPException(
                status_code=409,
                detail=f"Session {session_id}: input not complete."
            )

    # Start all sessions in background threads (batch executor will run
    # up to max_parallel concurrently)
    for session_id in req.session_ids:
        existing = _threads.get(session_id)
        if existing and existing.is_alive():
            raise HTTPException(
                status_code=409,
                detail=f"Session {session_id} already running."
            )
        t = threading.Thread(target=_safe_run, args=(session_id,), daemon=True)
        _threads[session_id] = t
        t.start()

    logger.info(
        f"Batch execution started: {len(req.session_ids)} sessions "
        f"(max {req.max_parallel} parallel)"
    )

    return {
        "started": True,
        "batch_size": len(req.session_ids),
        "session_ids": req.session_ids,
        "max_parallel": req.max_parallel,
    }
