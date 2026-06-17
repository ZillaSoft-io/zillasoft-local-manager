"""Orchestrator — the pipeline loop (spec §3, §7, §8.0).

Per session: pre-flight -> dry-run handshake (Sonnet plan, Haiku validate) ->
cycle loop (Opus implements via bash, Sonnet tests + reviews) -> on pass, await
Mario's approval; after `max_cycles` failures, escalate to Mario.

Kill and pause signals (from SessionController) are polled between steps. Cost
is tracked per session via a fresh UsageTracker from the agent factory.

AGENT REGISTRY: Orchestration roles are configurable via the agent registry.
To swap agents (e.g., use Mythos 5 for implementation instead of Opus):

    from app.agents.registry import get_registry
    registry = get_registry()
    registry.set_orchestration_roles(
        validation="haiku",      # plan validation (fast, cheap)
        planning="sonnet",       # plan generation (balanced)
        implementation="mythos"  # implementation (when Mythos 5 launches)
    )

No code changes needed. The orchestrator automatically uses the registered agents.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from .agents.phase2_orchestration import run_phase2_orchestration
from .agents.ml_routing import get_ml_router
from .cache import SessionCache
from .change_complexity import get_change_analyzer
from .cost import record_session_cost
from .cost.budgeting import BudgetManager
from .effort_routing import get_effort_router, EffortLevel
from .execution import run_tests
from .execution.executor import CommandStopped
from .feedback_loop import get_feedback_loop
from .observability import get_observability
from .persistent_cache import get_persistent_cache
from .testing_router import get_test_analyzer
from .vcs import GitOps

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config, db, audit, *, controller, executor, preflight,
                 budget, notifier, agent_factory: Callable, provisioner=None,
                 run_existing_tests: bool = True):
        self._config = config
        self._db = db
        self._audit = audit
        self._controller = controller
        self._executor = executor
        self._preflight = preflight
        self._budget = budget
        self._notifier = notifier
        self._agent_factory = agent_factory
        self._provisioner = provisioner
        self._run_existing_tests = run_existing_tests
        # Phase 3 managers
        self._ml_router = get_ml_router()
        self._persistent_cache = get_persistent_cache()
        self._observability = get_observability()
        self._feedback_loop = get_feedback_loop()

    # ------------------------------------------------------------------ #
    def run_session(self, session_id: str) -> dict:
        session = self._db.get_session(session_id)
        if session is None:
            raise KeyError(f"No session {session_id}")
        project = session.get("project")
        task_type = session.get("task_type")
        ctx = (session.get("haiku_context") or {}).get("summary") or ""

        # Phase 3: Budget check (prevent overspend)
        with self._observability.tracer.span("budget_check"):
            if not self._budget.can_accept_task():
                return {
                    "status": "rejected",
                    "reason": f"Budget limit reached: ${self._budget.current_spend:.2f} / ${self._budget.monthly_cap:.2f}",
                }

        haiku, sonnet, opus, tracker = self._agent_factory()
        repo_path, test_command = self._target(session)

        # ---- pre-flight ----
        pf_repo = repo_path if task_type != "new_app" else None
        pf = self._preflight.session(
            repo_path=pf_repo, test_command=test_command, session_id=session_id,
            run_existing_tests=self._run_existing_tests)
        self._audit.update(session_id, project, {"preflight": pf.as_dict()})

        # Capture the base SHA so a later reject can discard the session's
        # local commits (reset --hard back to here).
        if task_type != "new_app" and repo_path:
            base_sha = GitOps(repo_path, self._executor).head_sha()
            if base_sha:
                self._db.update_session(
                    session_id, deployment_status={"base_sha": base_sha})

        self._db.update_session(session_id, status="in_progress")

        # ---- Phase 2: plan + route + execute ----
        max_cycles = int(self._config.get("LOCAL_MANAGER_MAX_CYCLES", 3) or 3)
        cache = SessionCache()
        with self._observability.tracer.span("phase2_orchestration", project=project, task_type=task_type):
            dr = run_phase2_orchestration(
                haiku=haiku,
                sonnet=sonnet,
                opus=opus,
                context=ctx,
                original_intent=ctx,
                session_id=session_id,
                max_rounds=max_cycles,
                cache=cache,
            )

        # Store plan, routing decision, cost breakdown
        self._db.update_session(session_id, sonnet_instructions={
            "plan": dr.plan,
            "instructions": dr.implementation,
            "approved": dr.approved,
            "routing_decision": dr.routing_decision,
        })

        # Store cost breakdown
        if dr.cost_breakdown:
            self._db.update_session(session_id,
                cost_breakdown=dr.cost_breakdown.to_dict(),
                total_cost=dr.cost_breakdown.total_cost_usd,
                total_tokens_used=dr.cost_breakdown.total_tokens,
            )

        if not dr.approved:
            return self._escalate(session_id, project, tracker,
                                  f"Dry-run plan not approved by Haiku. "
                                  f"Error: {dr.error}")

        # For Opus-routed tasks, skip cycle loop (already implemented)
        if dr.routing_decision == "opus":
            instructions = dr.implementation
        else:
            # For Haiku-routed simple tasks, treat as complete
            return self._finish(session, project, tracker, cycle=0)

        # ---- cycle loop ----
        for cycle in range(1, max_cycles + 1):
            if self._controller.should_pause(session_id):
                return self._on_pause(session_id, project, tracker, cycle,
                                      instructions)
            if self._controller.should_stop(session_id):
                return self._on_stop(session_id, project, tracker, repo_path)

            try:
                opus_result = opus.implement_with_tools(
                    instructions, repo_path=repo_path, executor=self._executor,
                    session_id=session_id, controller=self._controller)
            except CommandStopped:
                opus_result = None

            if opus_result is None or opus_result.stopped:
                if self._controller.should_pause(session_id):
                    return self._on_pause(session_id, project, tracker, cycle,
                                          instructions)
                return self._on_stop(session_id, project, tracker, repo_path)

            opus_summary = sonnet.summarize_opus_output(
                opus_result.text or "(no summary provided)")

            # Intelligent test routing: analyze what changed to decide who runs tests
            change_analyzer = get_change_analyzer()
            diff = change_analyzer.get_diff(repo_path, opus_result.last_commit_sha)
            test_runner = change_analyzer.get_test_runner(diff, haiku, sonnet)
            test_runner_name = "haiku" if test_runner == haiku else "sonnet"

            # The final test run is short — not cancellable (the cancellable
            # unit is Opus's bash loop, checked above and between its steps).
            test_result = run_tests(self._executor, repo_path, test_command)

            # Intelligent review routing: analyze test results to decide who reviews
            test_analyzer = get_test_analyzer()
            review_agent = test_analyzer.get_routed_agent(
                test_result.tail() or "", test_result.ok, haiku, sonnet)

            # Intelligent effort routing: control thinking depth based on complexity
            # (only for Sonnet/Opus, Haiku does not support extended thinking)
            effort_router = get_effort_router()
            review_effort = effort_router.analyze_task_complexity(
                task_type="test_review",
                has_failures=not test_result.ok,
                change_complexity="simple" if test_runner_name == "haiku" else "complex"
            )
            effort_config = effort_router.get_effort_config(review_effort)

            # Build kwargs for review call
            review_kwargs = {
                "opus_summary": opus_summary,
                "test_summary": test_result.summary,
                "passed": test_result.ok
            }

            # Only Sonnet/Opus support thinking budget; Haiku does not
            if review_agent_name in ("sonnet", "opus"):
                review_kwargs["thinking_budget_tokens"] = effort_config["thinking_budget_tokens"]

            review = review_agent.review_after_tests(**review_kwargs)

            # Track which agents ran and reviewed tests, and effort levels used
            review_agent_name = "haiku" if review_agent == haiku else "sonnet"
            logger.info(
                f"Tests run by {test_runner_name}, "
                f"reviewed by {review_agent_name} ({review_effort.value} effort)"
            )

            self._audit.append_cycle(session_id, project, {
                "cycle_num": cycle,
                "opus": {"commands": opus_result.commands,
                         "steps": opus_result.steps,
                         "commit_sha": opus_result.last_commit_sha},
                "testing": {
                    "runner": test_runner_name,
                    "reviewer": review_agent_name,
                    "review_effort": review_effort.value,
                    "thinking_budget_tokens": effort_config["thinking_budget_tokens"],
                    "summary": test_result.summary,
                    "passed": test_result.ok,
                    "review": review
                },
            })
            self._db.update_session(
                session_id, cycle_count=cycle,
                opus_changes={"commands": opus_result.commands,
                              "commit_sha": opus_result.last_commit_sha},
                testing={
                    "runner": test_runner_name,
                    "reviewer": review_agent_name,
                    "review_effort": review_effort.value,
                    "thinking_budget_tokens": effort_config["thinking_budget_tokens"],
                    "review": review,
                    "test_summary": test_result.summary,
                    "passed": test_result.ok
                })

            if test_result.ok:
                return self._finish(session, project, tracker, cycle)

            # Failure -> record for feedback loop
            error_msg = test_result.tail()[:200] if test_result.tail() else "Test failed"
            self._feedback_loop.record_failure(
                error_msg=error_msg,
                project=project,
                agent=session.get("routing_decision", "unknown"),
                task_type=task_type or "unknown",
            )

            # Check if we should escalate instead of retrying
            if self._feedback_loop.should_escalate(error_msg, project, cycle, max_cycles):
                return self._escalate(
                    session_id, project, tracker,
                    f"Test failed with known pattern (seen before). "
                    f"Escalating instead of retry #{cycle + 1}.")

            # Failure -> new task for Opus (not a retry).
            instructions = sonnet.bug_from_failure(
                instructions=instructions, test_output=test_result.tail())

        return self._escalate(
            session_id, project, tracker,
            f"Reached cycle limit ({max_cycles}) without passing tests.")

    # ------------------------------------------------------------------ #
    # Targets
    # ------------------------------------------------------------------ #
    def _target(self, session: dict) -> tuple[str, str]:
        project = session.get("project")
        if session.get("task_type") == "new_app" or not project:
            scaffold = self._scaffold_dir(session)
            stack = ((session.get("haiku_context") or {}).get(
                "recommended_stack") or "").lower()
            if any(t in stack for t in ("astro", "typescript", "node")):
                return str(scaffold), "npm install && npm run build"
            return str(scaffold), "pytest tests/ -v"
        up = project.upper()
        return (self._config.get_raw(f"PROJECT_{up}_REPO_PATH"),
                self._config.get_raw(f"PROJECT_{up}_TEST_COMMAND", ""))

    def _scaffold_dir(self, session: dict) -> Path:
        base = self._config.get_raw("PROJECT_WEBSITE_REPO_PATH")
        parent = Path(base).parent if base else self._config.root
        return parent / ("new-app-" + session["id"][:8])

    # ------------------------------------------------------------------ #
    # Outcomes
    # ------------------------------------------------------------------ #
    def _record_cost(self, session_id: str, project: Optional[str], tracker):
        report = record_session_cost(self._db, self._audit, session_id, project,
                                     tracker, budget=None)
        before = self._budget.spent
        after = self._budget.record_spend(report.total)
        for t in self._budget.thresholds_crossed(before, after):
            self._notifier.desktop(
                "Cost warning",
                f"{int(t * 100)}% of the monthly cap reached "
                f"(${after:.2f} / ${self._budget.cap:.2f}).")

        # Export observability data before resetting
        obs_data = self._observability.export_all()
        self._audit.update(session_id, project, {"observability": obs_data})
        self._observability.reset()

        return report

    def _finish(self, session: dict, project: Optional[str], tracker,
                cycle: int) -> dict:
        session_id = session["id"]
        report = self._record_cost(session_id, project, tracker)

        # Phase 3: Record success to ML router (learns which agent works best)
        if project and session.get("routing_decision"):
            agent = session.get("routing_decision")
            self._ml_router.record_task(
                project=project,
                agent=agent,
                success=True,
                cost_usd=report.total,
                duration_ms=0,  # Would need start time to calculate
            )

        # New apps: auto-configure (.env section + setup log) on success.
        setup_log = None
        if session.get("task_type") == "new_app" and self._provisioner:
            try:
                prov = self._provisioner.provision(session, create_repo=False)
                setup_log = prov.get("setup_log")
            except Exception as exc:  # provisioning is non-fatal
                logger.warning("Provisioning failed for %s: %s", session_id, exc)

        self._db.update_session(session_id, status="awaiting_approval")
        self._notifier.notify(
            "approval",
            title="Approval needed",
            message=f"Session {session_id[:8]} passed tests after {cycle} "
                    f"cycle(s). Cost ${report.total:.2f}. Review to deploy.")
        logger.info("Session %s awaiting approval (cost $%.2f).",
                    session_id, report.total)
        result = {"status": "awaiting_approval", "cycles": cycle,
                  "cost": report.total}
        if setup_log:
            result["setup_log"] = setup_log
        return result

    def _escalate(self, session_id: str, project: Optional[str], tracker,
                  reason: str) -> dict:
        report = self._record_cost(session_id, project, tracker)

        # Phase 3: Record failure to ML router (learns which agent works best)
        session = self._db.get_session(session_id)
        if session and project and session.get("routing_decision"):
            agent = session.get("routing_decision")
            self._ml_router.record_task(
                project=project,
                agent=agent,
                success=False,
                cost_usd=report.total,
                duration_ms=0,
            )

        self._db.update_session(session_id, status="failed",
                                error_message=reason)
        self._audit.update(session_id, project, {"escalation": {"reason": reason}})
        self._notifier.notify(
            "escalation",
            title="Escalation — needs you",
            message=f"Session {session_id[:8]} escalated: {reason}",
            email_subject="ZillaSoft Local Manager — escalation",
            email_html=f"<p>{reason}</p>")
        logger.info("Session %s escalated: %s", session_id, reason)
        return {"status": "escalated", "reason": reason, "cost": report.total}

    def _on_stop(self, session_id: str, project: Optional[str], tracker,
                 repo_path: str) -> dict:
        # Commit whatever is in the working tree before stopping (best-effort).
        commit_sha = None
        try:
            self._executor.run(
                'git add -A && git commit -m "WIP: stopped via kill switch" '
                '|| true', cwd=repo_path)
            sha = self._executor.run("git rev-parse HEAD", cwd=repo_path)
            commit_sha = sha.stdout.strip() if sha.ok else None
        except Exception:
            pass
        self._record_cost(session_id, project, tracker)
        self._controller.kill(session_id, reason="kill switch",
                              commit_sha=commit_sha)
        return {"status": "stopped", "commit_sha": commit_sha}

    def _on_pause(self, session_id: str, project: Optional[str], tracker,
                  cycle: int, instructions: str) -> dict:
        report = self._record_cost(session_id, project, tracker)
        self._controller.save_pause(session_id, snapshot={
            "cycle": cycle, "instructions": instructions,
            "cost_so_far": report.total})
        return {"status": "paused", "cycle": cycle}
