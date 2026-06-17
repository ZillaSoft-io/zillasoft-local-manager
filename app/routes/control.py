"""Cost + control API (Phase 4): kill switch, pause/resume, cost snapshots.

Routes (all require auth):
  POST /api/sessions/{id}/kill        stop a session (kill switch)
  POST /api/sessions/{id}/pause       pause + serialize snapshot
  POST /api/sessions/{id}/resume      restore a paused session
  GET  /api/paused                    list resumable sessions
  GET  /api/sessions/{id}/cost        a session's stored cost breakdown
  GET  /api/cost                      monthly budget snapshot
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["control"],
                   dependencies=[Depends(_main.require_auth)])


class KillBody(BaseModel):
    reason: str = ""


class PauseBody(BaseModel):
    snapshot: dict = {}


def _controller():
    c = getattr(_main.state, "controller", None)
    if c is None:
        raise HTTPException(status_code=503, detail="Controller not ready.")
    return c


# --------------------------- kill / pause / resume --------------------------- #
@router.post("/sessions/{session_id}/kill")
async def kill(session_id: str, body: KillBody):
    try:
        return _controller().kill(session_id, reason=body.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")


@router.post("/sessions/{session_id}/pause")
async def pause(session_id: str, body: PauseBody):
    try:
        _controller().save_pause(session_id, snapshot=body.snapshot)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"status": "paused", "session_id": session_id}


@router.post("/sessions/{session_id}/resume")
async def resume(session_id: str):
    try:
        record = _controller().resume(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "resumed", "session_id": session_id, "record": record}


@router.get("/paused")
async def list_paused():
    return {"paused": _controller().list_resumable()}


# --------------------------- cost --------------------------- #
@router.get("/cost")
async def monthly_cost():
    budget = getattr(_main.state, "budget", None)
    if budget is None:
        raise HTTPException(status_code=503, detail="Budget not ready.")
    return budget.snapshot()


@router.get("/sessions/{session_id}/cost")
async def session_cost(session_id: str):
    session = _main.state.db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {
        "session_id": session_id,
        "total_cost": session.get("total_cost"),
        "total_tokens_used": session.get("total_tokens_used"),
        "cost_breakdown": session.get("cost_breakdown"),
    }
