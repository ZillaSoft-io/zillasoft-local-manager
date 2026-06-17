"""Approval / git API (Phase 6).

  POST /api/sessions/{id}/approve    finalize + push to GitHub
  POST /api/sessions/{id}/reject     discard the session's local commits
  POST /api/sessions/{id}/rollback   revert the pushed commit and push the revert
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..errors import ConfigValidationError
from ..release import ReleaseError
from ..vcs import GitError

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api", tags=["release"])


class ReviewBody(BaseModel):
    notes: str = ""


def _rel():
    from .. import main
    r = getattr(main.state, "release", None)
    if r is None:
        raise HTTPException(status_code=503, detail="Release manager not ready.")
    return r


def _guarded(fn):
    try:
        return fn()
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")
    except ConfigValidationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (ReleaseError, GitError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions/{session_id}/approve")
async def approve(session_id: str, body: ReviewBody):
    result = _guarded(lambda: _rel().approve(session_id, notes=body.notes))
    # Push succeeded — start monitoring the deployment in the background.
    from .deploy import start_deploy
    start_deploy(session_id)
    return result


@router.post("/sessions/{session_id}/reject")
async def reject(session_id: str, body: ReviewBody):
    return _guarded(lambda: _rel().reject(session_id, notes=body.notes))


@router.post("/sessions/{session_id}/rollback")
async def rollback(session_id: str):
    return _guarded(lambda: _rel().rollback(session_id))
