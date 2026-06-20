"""Config write API (Phase 9 settings panel).

This is the ONLY write path that may set credentials — it represents Mario
editing via the authed UI (actor='system' bypasses the agent credential block).
Agents never reach this HTTP endpoint; in code they call config.set(actor='agent')
which still raises on credential keys.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..errors import ConfigValidationError

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/config", tags=["config"])

# Imported at the bottom of main.py (after `state`); `_main.state` is
# only read at request time, so this is not a circular import.
from .. import main as _main  # noqa: E402


class SetBody(BaseModel):
    key: str
    value: str


@router.post("/set")
async def set_config(body: SetBody):
    cfg = _main.state.config
    try:
        cfg.set(body.key, body.value, actor="system")  # Mario via the UI
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "key": body.key,
            "is_credential": cfg.is_credential(body.key),
            "value": "<set>" if cfg.is_credential(body.key) else body.value}
