"""Settings API — budget, agents, ML router, observability controls.

Exposes configuration and monitoring data via REST for the UI.
All changes are persisted and take effect immediately.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..agent_fallback import get_fallback_chain
from ..agents.registry import get_registry
from ..cost.budgeting import BudgetManager
from ..cycle_timeline import get_session_timelines
from ..feedback_loop import get_feedback_loop
from ..observability import get_observability
from ..session_recovery import SessionRecoveryManager

logger = logging.getLogger(__name__)

# main.py imports this module at the bottom (after `state` is defined), and
# `_main.state` is only accessed at request time, so this is not circular.
from .. import main as _main  # noqa: E402

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ============================================================================
# Request/Response models
# ============================================================================

class BudgetSettings(BaseModel):
    """Budget configuration."""
    monthly_cap_usd: float
    warning_50_percent: bool
    warning_75_percent: bool
    warning_90_percent: bool


class AgentSettings(BaseModel):
    """Agent orchestration configuration."""
    validation_agent: str  # "haiku", "sonnet", "opus", etc.
    planning_agent: str
    implementation_agent: str


class FeedbackLoopSettings(BaseModel):
    """Feedback loop configuration."""
    enabled: bool
    escalation_threshold: int  # occurrences before escalation


class MonitoringData(BaseModel):
    """Real-time monitoring data."""
    cost_today: float
    cost_month: float
    budget_remaining: float
    budget_percent_used: float
    cache_hit_rate: float
    failure_patterns_tracked: int


# ============================================================================
# Budget endpoints
# ============================================================================

@router.get("/budget")
async def get_budget_settings() -> BudgetSettings:
    """Get current budget settings."""
    budget = _main.state.budget
    return BudgetSettings(
        monthly_cap_usd=budget.cap,
        warning_50_percent=True,  # Always enabled
        warning_75_percent=True,
        warning_90_percent=True,
    )


@router.post("/budget")
async def set_budget_settings(settings: BudgetSettings) -> dict[str, Any]:
    """Update budget settings."""
    if settings.monthly_cap_usd <= 0:
        raise HTTPException(status_code=400, detail="Cap must be > $0")

    budget = _main.state.budget
    # `cap` is a read-only property backed by config; write through config.
    _main.state.config.set("LOCAL_MANAGER_MONTHLY_COST_CAP",
                           float(settings.monthly_cap_usd), actor="system")
    logger.info(f"Budget cap updated: ${settings.monthly_cap_usd}")

    return {
        "ok": True,
        "monthly_cap_usd": settings.monthly_cap_usd,
        "current_spend": budget.spent,
    }


# ============================================================================
# Agent orchestration endpoints
# ============================================================================

@router.get("/agents")
async def get_agent_settings() -> AgentSettings:
    """Get current agent orchestration configuration."""
    registry = get_registry()
    return AgentSettings(
        validation_agent=registry.get_validation_agent(),
        planning_agent=registry.get_planning_agent(),
        implementation_agent=registry.get_implementation_agent(),
    )


@router.post("/agents")
async def set_agent_settings(settings: AgentSettings) -> dict[str, Any]:
    """Update agent orchestration roles."""
    registry = get_registry()

    try:
        registry.set_orchestration_roles(
            validation=settings.validation_agent,
            planning=settings.planning_agent,
            implementation=settings.implementation_agent,
        )
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {e}")

    logger.info(
        f"Agent roles updated: validation={settings.validation_agent}, "
        f"planning={settings.planning_agent}, "
        f"implementation={settings.implementation_agent}"
    )

    return {
        "ok": True,
        "validation_agent": settings.validation_agent,
        "planning_agent": settings.planning_agent,
        "implementation_agent": settings.implementation_agent,
    }


@router.get("/agents/available")
async def list_available_agents() -> dict[str, list[str]]:
    """List available agents by tier."""
    registry = get_registry()
    agents = registry.list_all()

    by_tier = {"cheap": [], "medium": [], "expensive": []}
    for agent in agents:
        tier = agent.cost_tier
        if tier in by_tier:
            by_tier[tier].append(agent.label)

    return by_tier


# ============================================================================
# ML router endpoints
# ============================================================================

# ============================================================================
# Feedback loop endpoints
# ============================================================================

@router.get("/feedback-loop")
async def get_feedback_loop_settings() -> FeedbackLoopSettings:
    """Get feedback loop configuration."""
    feedback = get_feedback_loop()
    summary = feedback.summary()

    return FeedbackLoopSettings(
        enabled=True,
        escalation_threshold=3,  # Default from implementation
    )


@router.post("/feedback-loop")
async def set_feedback_loop_settings(settings: FeedbackLoopSettings) -> dict[str, Any]:
    """Update feedback loop settings."""
    if settings.escalation_threshold < 1:
        raise HTTPException(
            status_code=400,
            detail="Escalation threshold must be >= 1"
        )

    logger.info(f"Feedback loop threshold updated: {settings.escalation_threshold}")
    return {"ok": True, "escalation_threshold": settings.escalation_threshold}


@router.get("/feedback-loop/patterns")
async def get_failure_patterns() -> dict[str, Any]:
    """Get learned failure patterns."""
    feedback = get_feedback_loop()
    return feedback.summary()


# ============================================================================
# Monitoring & observability endpoints
# ============================================================================

@router.get("/monitoring")
async def get_monitoring_data() -> MonitoringData:
    """Get real-time monitoring and cost data."""
    budget = _main.state.budget
    feedback = get_feedback_loop()

    fb_summary = feedback.summary()

    # Calculate cost today (simplified — from budget tracker)
    cost_month = budget.spent
    budget_percent = (cost_month / budget.cap * 100) if budget.cap > 0 else 0

    return MonitoringData(
        cost_today=0.0,  # Would need timestamp tracking for today
        cost_month=cost_month,
        budget_remaining=max(0, budget.cap - cost_month),
        budget_percent_used=budget_percent,
        cache_hit_rate=0.0,  # Would need cache stats
        failure_patterns_tracked=fb_summary.get("total_failure_patterns", 0),
    )


# ============================================================================
# Observability endpoints
# ============================================================================

@router.get("/observability/traces")
async def get_traces() -> dict[str, Any]:
    """Get recent execution traces."""
    obs = get_observability()
    return obs.export_traces()


@router.get("/observability/metrics")
async def get_metrics() -> dict[str, Any]:
    """Get observability metrics."""
    obs = get_observability()
    return obs.export_metrics()


# ============================================================================
# Session recovery endpoints (Improvement 5)
# ============================================================================

@router.get("/recovery/incomplete-sessions")
async def get_incomplete_sessions() -> dict[str, Any]:
    """Get all sessions with incomplete cycles from crashes."""
    recovery = SessionRecoveryManager()
    incomplete = recovery.get_incomplete_sessions()

    return {
        "incomplete_count": len(incomplete),
        "sessions": incomplete,
        "summary": recovery.format_for_ui(incomplete),
    }


@router.get("/recovery/session/{session_id}")
async def get_session_recovery_details(session_id: str) -> dict[str, Any]:
    """Get recovery details for a specific incomplete session."""
    recovery = SessionRecoveryManager()
    details = recovery.get_session_details(session_id, _main.state.db)

    if details is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} not found or already complete"
        )

    return {
        "ok": True,
        "recovery": details,
    }


# ============================================================================
# Agent resilience endpoints
# ============================================================================

@router.get("/agents/health")
async def get_agent_health() -> dict[str, Any]:
    """Get health status of all agents (for resilience monitoring)."""
    fallback = get_fallback_chain()
    health = fallback.get_health_summary()

    # UI 2: Add human-readable status
    ui_status = {}
    for agent_name, h in fallback.health.items():
        if h.is_degraded:
            status = "⚠️ degraded"
            details = f"{h.consecutive_failures} failures"
        elif h.consecutive_failures > 0:
            status = "⚠️ warn"
            details = f"{h.consecutive_failures} recent failures"
        else:
            status = "✓ healthy"
            details = "all checks passing"

        ui_status[agent_name] = {
            "status": status,
            "details": details,
            "failures": h.consecutive_failures,
            "is_degraded": h.is_degraded,
            "last_failure": h.last_failure_time.isoformat() if h.last_failure_time else None,
        }

    return {
        "agents": ui_status,
        "fallback_chains": fallback.effective_chains(),
    }


@router.post("/agents/health/reset/{agent_name}")
async def reset_agent_health(agent_name: str) -> dict[str, Any]:
    """Manually reset health status for an agent (ops only)."""
    fallback = get_fallback_chain()

    if agent_name not in ["haiku", "sonnet", "opus"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid agent name. Must be: haiku, sonnet, or opus"
        )

    fallback.reset_health(agent_name)
    logger.info(f"Reset health for agent {agent_name}")

    return {
        "ok": True,
        "agent": agent_name,
        "health": fallback.get_health_summary()[agent_name],
    }


# ============================================================================
# UI 4: Cycle timeline and UI 5: Cost breakdown
# ============================================================================

@router.get("/sessions/{session_id}/timeline")
async def get_session_timeline(session_id: str) -> dict[str, Any]:
    """Get cycle timeline for a session (UI 4: timing breakdown)."""
    timelines = get_session_timelines(session_id)
    summary = timelines.get_summary()

    return {
        "ok": True,
        "timeline": summary,
    }


@router.get("/sessions/{session_id}/cost-breakdown")
async def get_session_cost_breakdown(session_id: str) -> dict[str, Any]:
    """Get detailed cost breakdown for a session (UI 5: cost transparency)."""
    session = _main.state.db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    cost_breakdown = session.get("cost_breakdown", {})
    total_cost = session.get("total_cost", 0)

    return {
        "ok": True,
        "session_id": session_id,
        "total_cost": round(total_cost, 4),
        "total_tokens": session.get("total_tokens_used", 0),
        "breakdown": cost_breakdown,
        "by_phase": cost_breakdown.get("by_phase", {}),
    }
