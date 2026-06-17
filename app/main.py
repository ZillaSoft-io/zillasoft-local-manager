"""FastAPI application — Phase 1 core (server, config, DB, audit, auth).

Serves on localhost:5555. Later phases add the agent orchestration routes,
cost tracking, deployment tracking, and the web UI.
"""
from __future__ import annotations

print("[STARTUP] Importing modules...")

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header

print("[STARTUP] FastAPI imported")

from . import __version__
from .agents import build_agents
from .audit import AuditTrail
from .auth import make_auth_dependency
from .config import ConfigHandler
from .control import SessionController
from .cost import MonthlyBudget
from .database import Database
from .deploy import DeploymentTracker
from .execution import CodeExecutor, PreFlight
from .health_monitor import start_health_monitoring, stop_health_monitoring
from .input import ConversationManager
from .newapp import NewAppProvisioner
from .notifications import Notifier
from .orchestrator import Orchestrator
from .release import ReleaseManager

print("[STARTUP] All modules imported successfully")

logger = logging.getLogger(__name__)


class AppState:
    """Container for the shared singletons (config, db, audit)."""

    config: ConfigHandler
    db: Database
    audit: AuditTrail
    auth_token: str
    conversation: ConversationManager
    haiku: object
    sonnet: object
    opus: object
    usage: object
    budget: MonthlyBudget
    notifier: Notifier
    controller: SessionController
    executor: CodeExecutor
    preflight: PreFlight
    orchestrator: Orchestrator
    release: ReleaseManager
    provisioner: NewAppProvisioner
    deploy_tracker: DeploymentTracker


state = AppState()


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        print("[STARTUP] Lifespan startup starting...")
        config = ConfigHandler()
        _configure_logging(config.get_raw("LOCAL_MANAGER_LOG_LEVEL", "INFO"))
        logger.info("ZillaSoft Local Manager v%s starting up.", __version__)

        state.config = config
        state.db = Database(config.resolve_path("LOCAL_MANAGER_DB_PATH", "./local_manager.db"))
        audit_dir = config.resolve_path("LOCAL_MANAGER_AUDIT_LOG_PATH", "./audit_logs/")
        state.audit = AuditTrail(audit_dir)
        state.auth_token = config.ensure_auth_token()

        mock_mode = config.get_raw("LOCAL_MANAGER_MOCK_MODE", "false").lower() == "true"
        if mock_mode:
            logger.info("MOCK MODE ENABLED")

        haiku, sonnet, opus, usage = build_agents(config, mock_mode=mock_mode)
        state.haiku, state.sonnet, state.opus, state.usage = haiku, sonnet, opus, usage
        state.conversation = ConversationManager(config, state.db, state.audit, haiku)

        state.budget = MonthlyBudget(config)
        if state.budget.maybe_reset():
            logger.info("Monthly spend auto-reset on startup.")
        state.notifier = Notifier(config)
        state.controller = SessionController(config, state.db, state.audit, pause_dir=config.root / "paused", notifier=state.notifier)
        swept = state.controller.sweep_expired()
        if swept:
            logger.info("Swept %d expired paused session(s) on startup.", swept)

        state.executor = CodeExecutor(controller=state.controller)
        state.preflight = PreFlight(config, state.executor)
        state.provisioner = NewAppProvisioner(config, state.db, state.audit)

        def get_cached_agents():
            return state.haiku, state.sonnet, state.opus, state.usage

        state.orchestrator = Orchestrator(config, state.db, state.audit, controller=state.controller, executor=state.executor, preflight=state.preflight, budget=state.budget, notifier=state.notifier, agent_factory=get_cached_agents, provisioner=state.provisioner)
        state.release = ReleaseManager(config, state.db, state.audit, state.executor, state.notifier, haiku=state.haiku)
        state.deploy_tracker = DeploymentTracker(config, state.db, state.audit, state.notifier)
        logger.info("Startup preflight: %s", state.preflight.startup())

        logger.info("DB ready. Audit logs at %s", audit_dir)
        logger.info("API auth token: %s", state.auth_token)

        await start_health_monitoring(state.haiku.client)
        print("[STARTUP] App ready on http://localhost:5555")

    except Exception as e:
        print(f"[ERROR] Startup failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        raise

    yield

    # ---- shutdown ----
    # Stability 4: Graceful shutdown — save checkpoint on SIGTERM
    logger.info("ZillaSoft Local Manager shutting down.")
    await stop_health_monitoring()


app = FastAPI(
    title="ZillaSoft Local Manager",
    version=__version__,
    description="Multi-agent orchestration for ZillaSoft projects.",
    lifespan=lifespan,
)


async def require_auth(authorization: str | None = Header(default=None)):
    """Auth dependency — skipped for local dev, enforced in production."""
    # Skip auth requirement for local development
    # Enable auth when deploying to Railway
    if not state.config.get_raw("LOCAL_MANAGER_REQUIRE_AUTH", "false").lower() == "true":
        return None  # Local dev mode: no auth required
    verify = make_auth_dependency(state.config)
    return await verify(authorization)


# --------------------------------------------------------------------------- #
# Public probe (no auth) — used by the startup health checks / readiness.
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "zillasoft-local-manager",
            "version": __version__}


@app.get("/", include_in_schema=False)
async def ui():
    """Serve the single-page UI shell (auth happens client-side via token)."""
    from fastapi.responses import FileResponse
    from pathlib import Path
    return FileResponse(Path(__file__).resolve().parent / "ui" / "index.html")


# --------------------------------------------------------------------------- #
# Authenticated API
# --------------------------------------------------------------------------- #
@app.get("/api/status", tags=["system"], dependencies=[Depends(require_auth)])
async def api_status():
    return {
        "service": "zillasoft-local-manager",
        "version": __version__,
        "db_path": str(state.db.db_path),
        "audit_path": str(state.audit.base_path),
        "monthly_cost_cap": state.config.get("LOCAL_MANAGER_MONTHLY_COST_CAP"),
        "current_month_spent": state.config.get(
            "LOCAL_MANAGER_CURRENT_MONTH_SPENT"),
        "auto_commit": state.config.get("LOCAL_MANAGER_AUTO_COMMIT"),
        "auto_deploy": state.config.get("LOCAL_MANAGER_AUTO_DEPLOY"),
    }


@app.get("/api/config", tags=["config"], dependencies=[Depends(require_auth)])
async def api_config():
    """Config snapshot for the UI. Credentials are masked (<set>/<unset>)."""
    return state.config.snapshot(redact_credentials=True)


@app.get("/api/sessions", tags=["sessions"],
         dependencies=[Depends(require_auth)])
async def api_sessions(limit: int = 20, project: str | None = None):
    return state.db.list_sessions(limit=limit, project=project)


@app.get("/api/sessions/{session_id}", tags=["sessions"],
         dependencies=[Depends(require_auth)])
async def api_session(session_id: str):
    session = state.db.get_session(session_id)
    if session is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found.")
    return session


# Mounted last so `require_auth` and `state` are defined before the router
# module imports this one (avoids a circular import at module load).
from .routes.input import router as input_router  # noqa: E402
from .routes.control import router as control_router  # noqa: E402
from .routes.pipeline import router as pipeline_router  # noqa: E402
from .routes.release import router as release_router  # noqa: E402
from .routes.newapp import router as newapp_router  # noqa: E402
from .routes.deploy import router as deploy_router  # noqa: E402
from .routes.config import router as config_router  # noqa: E402
from .routes.settings import router as settings_router  # noqa: E402
from .routes.changelog import router as changelog_router  # noqa: E402

app.include_router(input_router)
app.include_router(control_router)
app.include_router(pipeline_router)
app.include_router(release_router)
app.include_router(newapp_router)
app.include_router(deploy_router)
app.include_router(config_router)
app.include_router(settings_router)
app.include_router(changelog_router)

if __name__ == "__main__":
    import uvicorn
    print("[STARTUP] Starting uvicorn server...")
    uvicorn.run(app, host="localhost", port=5555)
