"""New-app config API (Phase 7).

  POST /api/sessions/{id}/create-repo   create the GitHub repo (Mario-authorized)
                                        and (re)register its .env section
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..errors import ConfigValidationError
from ..integrations import GitHubError

logger = logging.getLogger(__name__)

from .. import main as _main  # noqa: E402

router = APIRouter(prefix="/api", tags=["newapp"],
                   dependencies=[Depends(_main.require_auth)])


@router.post("/sessions/{session_id}/create-repo")
async def create_repo(session_id: str):
    prov = getattr(_main.state, "provisioner", None)
    if prov is None:
        raise HTTPException(status_code=503, detail="Provisioner not ready.")
    session = _main.state.db.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.get("task_type") != "new_app":
        raise HTTPException(status_code=400, detail="Not a new-app session.")
    try:
        result = prov.provision(session, create_repo=True)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GitHubError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": result["name"], "repo": result["repo"],
            "setup_log": result["setup_log"]}
