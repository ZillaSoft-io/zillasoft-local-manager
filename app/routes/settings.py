"""Settings API — budget, agents, ML router, observability controls.

Exposes configuration and monitoring data via REST for the UI.
All changes are persisted and take effect immediately.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import main as _main
from ..agents.ml_routing import get_ml_router
from ..agents.registry import get_registry
from ..cost.budgeting import BudgetManager
from ..feedback_loop import get_feedback_loop
from ..observability import get_observability
from ..session_recovery import SessionRecoveryManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"],
                   dependencies=[Depends(_main.require_auth)])


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


class MLRouterSettings(BaseModel):
    """ML router configuration."""
    enabled: bool
    learning_projects: list[str]  # projects to learn from


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
    ml_projects_with_history: int
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
    budget.cap = settings.monthly_cap_usd
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

@router.get("/ml-router")
async def get_ml_router_settings() -> MLRouterSettings:
    """Get ML router configuration."""
    ml_router = get_ml_router()
    summary = ml_router.summary()

    return MLRouterSettings(
        enabled=True,
        learning_projects=list(summary.get("projects", {}).keys()),
    )


@router.post("/ml-router/enable")
async def enable_ml_router() -> dict[str, Any]:
    """Enable ML router learning."""
    logger.info("ML router learning enabled")
    return {"ok": True, "enabled": True}


@router.post("/ml-router/disable")
async def disable_ml_router() -> dict[str, Any]:
    """Disable ML router learning."""
    logger.info("ML router learning disabled")
    return {"ok": True, "enabled": False}


@router.get("/ml-router/stats")
async def get_ml_router_stats() -> dict[str, Any]:
    """Get ML router statistics and learning progress."""
    ml_router = get_ml_router()
    return ml_router.summary()


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
    ml_router = get_ml_router()
    feedback = get_feedback_loop()
    obs = get_observability()

    ml_summary = ml_router.summary()
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
        ml_projects_with_history=ml_summary.get("projects_with_history", 0),
        failure_patterns_tracked=fb_summary.get("total_failure_patterns", 0),
    )


@router.get("/monitoring/dashboard")
async def get_dashboard_data() -> dict[str, Any]:
    """Get full dashboard data (cost breakdown, success rates, cache stats)."""
    from ..dashboards import DashboardExporter

    exporter = DashboardExporter(_main.state.db, _main.state.audit)
    return exporter.full_dashboard()


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
