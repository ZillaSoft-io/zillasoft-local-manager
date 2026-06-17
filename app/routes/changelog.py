"""Changelog API — trigger daily updates to the website changelog.

Can be called manually via HTTP, or scheduled daily via cron/scheduler.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..changelog_updater import get_changelog_updater

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/changelog", tags=["changelog"],
                   dependencies=[Depends(_main.require_auth)])


@router.post("/update")
async def trigger_changelog_update() -> dict[str, bool | str]:
    """Manually trigger daily changelog update.

    Scans commits from all ZillaSoft projects, summarizes with Haiku,
    and posts to the website changelog (max once per 24 hours).
    """
    try:
        updater = get_changelog_updater()
        # Set Haiku agent if available
        if hasattr(_main.state, 'haiku'):
            updater.haiku = _main.state.haiku

        success = updater.update_changelog()
        if success:
            return {"ok": True, "message": "Changelog updated successfully"}
        else:
            return {
                "ok": False,
                "message": "No new commits to add, or updated recently"
            }
    except Exception as e:
        logger.exception("Changelog update failed")
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


@router.get("/status")
async def get_changelog_status() -> dict[str, str | None]:
    """Get last changelog update timestamp."""
    try:
        updater = get_changelog_updater()
        state = updater._load_state()
        return {
            "last_update": state.get("last_update"),
            "last_date": state.get("last_date"),
            "ready": updater.should_update(),
        }
    except Exception as e:
        logger.error(f"Failed to get changelog status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedule-daily")
async def schedule_daily_update() -> dict[str, str]:
    """Schedule daily changelog updates (requires external scheduler).

    This endpoint documents how to set up automated daily updates.
    You can use:
    - Linux cron: `0 0 * * * curl -X POST http://localhost:5555/api/changelog/update -H "Authorization: Bearer <token>"`
    - Systemd timer
    - APScheduler in Python
    - GitHub Actions
    """
    return {
        "message": "Daily updates can be scheduled using your preferred scheduler",
        "example_cron": "0 0 * * * curl -X POST http://localhost:5555/api/changelog/update -H 'Authorization: Bearer <token>'",
        "note": "The endpoint enforces a 24-hour minimum between updates automatically"
    }
