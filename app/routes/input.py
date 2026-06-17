"""Chatbox / input-handling API (Phase 3).

Routes (all require auth):
  POST /api/input/sessions                       create a session, optional first message
  POST /api/input/sessions/{id}/messages         send a message, get Haiku's reply
  POST /api/input/sessions/{id}/attachments      upload a screenshot
  GET  /api/input/sessions/{id}/messages         full transcript
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ..errors import AgentError, ConfigValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/input", tags=["input"])

_MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB


class CreateSessionBody(BaseModel):
    task_type: str
    project: Optional[str] = None
    message: Optional[str] = None


class MessageBody(BaseModel):
    message: str
    attachments: list[dict] = []


def _get_state():
    """Lazy import to avoid circular dependency."""
    from .. import main
    return main.state


def _cm():
    cm = getattr(_get_state(), "conversation", None)
    if cm is None:
        raise HTTPException(status_code=503, detail="Input handler not ready.")
    return cm


@router.post("/sessions")
async def create_session(body: CreateSessionBody):
    cm = _cm()
    try:
        session_id = cm.create_session(body.task_type, body.project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = {"session_id": session_id}
    if body.message:
        result["turn"] = asdict(_run(cm, session_id, body.message))
    return result


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageBody):
    cm = _cm()
    turn = _run(cm, session_id, body.message,
                attachments=body.attachments or None)
    return {"turn": asdict(turn)}


def _run(cm, session_id: str, message: str, attachments=None):
    """Run one clarification turn, mapping known failures to clean HTTP errors."""
    try:
        return cm.handle_message(session_id, message, attachment_refs=attachments)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")
    except ConfigValidationError as exc:
        # e.g. ANTHROPIC_API_KEY not set yet — Mario fills it via the UI.
        raise HTTPException(status_code=503, detail=str(exc))
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/attachments")
async def upload_attachment(session_id: str, file: UploadFile):
    cm = _cm()
    if cm.db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 8 MB limit.")
    ref = cm.attachments.save(session_id, file.filename or "upload", data)
    return {"attachment": ref}


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    cm = _cm()
    if cm.db.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"messages": cm.transcript(session_id)}
