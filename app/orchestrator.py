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

from .agents.phase2_orchestration import run_phase2_orchestration, Phase2Result
from .agent_fallback import get_fallback_chain
from .cache import SessionCache
from .change_complexity import get_change_analyzer
from .cost import record_session_cost
from .cost.budgeting import BudgetManager
from .cost_estimation import get_cost_estimator
from .crash_recovery import get_crash_recovery
from .cycle_timeline import get_session_timelines, cleanup_session_timeline
from .effort_routing import get_effort_router, EffortLevel
from .escalation_messages import build_escalation_reason
from .execution import run_tests
from .execution.executor import CommandStopped
from .feedback_loop import get_feedback_loop
from .observability import get_observability
from .persistent_cache import get_persistent_cache
from .smart_retry import should_retry_with_refinement
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

        # Improvement 1: Cost estimation — predict cost before running
        with self._observability.tracer.span("cost_estimation"):
            estimator = get_cost_estimator()
            cost_estimate = estimator.estimate_session(
                task_complexity="complex" if task_type == "new_app" else "medium",
                expected_cycles=int(self._config.get("LOCAL_MANAGER_MAX_CYCLES", 3) or 3),
                simple_change=False
            )
            logger.info(f"Cost estimate: {estimator.format_estimate(cost_estimate)}")
            self._db.update_session(session_id, cost_estimate=cost_estimate)

        # Phase 3: Budget check (SOFT cap — warn but never block work in flight).
        # If we're already at/over the monthly cap we notify and proceed so the
        # task at hand can finish. Per-threshold (50/80/100%) warnings still fire
        # in _record_cost after the run.
        with self._observability.tracer.span("budget_check"):
            if self._budget.cap > 0 and self._budget.spent >= self._budget.cap:
                msg = (f"Monthly budget reached "
                       f"(${self._budget.spent:.2f} / ${self._budget.cap:.2f}). "
                       f"Proceeding anyway (soft cap).")
                logger.warning(msg)
                try:
                    self._notifier.desktop("Budget warning", msg)
                except Exception:
                    pass

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

        # Resume: if a previous run already produced a validated plan (stored in
        # sonnet_instructions), reuse it and SKIP the whole planning phase
        # (plan + up to N validation rounds + instructions) — the expensive
        # upfront LLM work. The implementation cycle still runs fresh, because a
        # half-finished agentic edit can't be safely continued; prior local
        # commits are preserved in git.
        stored = session.get("sonnet_instructions") or {}
        if stored.get("approved") and stored.get("plan"):
            logger.info("Resuming %s: reusing stored plan, skipping planning phase.",
                        session_id)
            dr = Phase2Result(
                plan=stored.get("plan", ""),
                routing_decision=stored.get("routing_decision", ""),
                implementation=stored.get("instructions", ""),
                approved=True, rounds=0, cost_breakdown=None,
                success=True, error="")
        else:
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
                                  reason="plan_rejected",
                                  context={"error": dr.error, "cycles": 1})

        # Always run the agentic cycle loop below — it is the only thing that
        # actually modifies the repo (implement_with_tools), runs tests, and
        # reviews. The earlier execution_phase only produced draft text, so a
        # session must never be marked "done" without going through the loop.
        # `dr.implementation` seeds the first round of instructions.
        instructions = dr.implementation or ""
        # Pick the implementation agent (by complexity) and effort (independent),
        # leading the fallback chain. Safe: any failure falls back to the
        # configured agent. See _select_impl_agent.
        impl_preferred, impl_effort = self._select_impl_agent(haiku, ctx, dr.plan)

        # ---- cycle loop with crash recovery ----
        recovery = get_crash_recovery()
        # UI 4: Track cycle timeline for transparency
        timelines = get_session_timelines(session_id)

        for cycle in range(1, max_cycles + 1):
            # Start cycle timeline
            cycle_timeline = timelines.start_cycle(cycle)
            if self._controller.should_pause(session_id):
                return self._on_pause(session_id, project, tracker, cycle,
                                      instructions)
            if self._controller.should_stop(session_id):
                return self._on_stop(session_id, project, tracker, repo_path)

            # Resilience: use fallback chain for implementation (in case Opus is down)
            fallback = get_fallback_chain()
            impl_calls = {
                "opus": lambda: opus.implement_with_tools(
                    instructions, repo_path=repo_path, executor=self._executor,
                    session_id=session_id, controller=self._controller,
                    effort=impl_effort),
                "sonnet": lambda: sonnet.implement_with_tools(
                    instructions, repo_path=repo_path, executor=self._executor,
                    session_id=session_id, controller=self._controller,
                    effort=impl_effort),
                "haiku": lambda: haiku.implement_with_tools(
                    instructions, repo_path=repo_path, executor=self._executor,
                    session_id=session_id, controller=self._controller,
                    effort=impl_effort),
            }

            # UI 4: Track implementation timing
            impl_step = cycle_timeline.add_step("implementation", "opus")

            try:
                opus_result, impl_agent = fallback.execute_with_fallback(
                    "implementation", impl_calls, preferred=impl_preferred
                )
                impl_step.agent = impl_agent
                impl_step.complete()
                if impl_agent != impl_preferred:
                    logger.warning(f"Implementation degraded: using {impl_agent} instead of {impl_preferred}")
                    # UI 3: Fallback notification (logged for UI display)
                    logger.info(f"FALLBACK: Implementation used {impl_agent} (primary {impl_preferred} unavailable)")
            except CommandStopped:
                opus_result = None
            except RuntimeError as e:
                logger.error(f"All agents failed for implementation: {e}")
                raise

            if opus_result is None or opus_result.stopped:
                if self._controller.should_pause(session_id):
                    return self._on_pause(session_id, project, tracker, cycle,
                                          instructions)
                return self._on_stop(session_id, project, tracker, repo_path)

            opus_summary = sonnet.summarize_opus_output(
                opus_result.text or "(no summary provided)")

            try:
                # Checkpoint before critical operations
                recovery.save_checkpoint(
                    session_id, cycle, "pre_test",
                    {"opus_summary": opus_summary,
                     "opus_commit": opus_result.last_commit_sha}
                )

                # Intelligent test routing: analyze what changed to decide who runs tests
                change_analyzer = get_change_analyzer()
                diff = change_analyzer.get_diff(repo_path, opus_result.last_commit_sha)
                test_runner = change_analyzer.get_test_runner(diff, haiku, sonnet)
                test_runner_name = "haiku" if test_runner == haiku else "sonnet"

                # The final test run is short — not cancellable (the cancellable
                # unit is Opus's bash loop, checked above and between its steps).
                # UI 4: Track test timing
                test_step = cycle_timeline.add_step("test_run", test_runner_name)
                test_result = run_tests(self._executor, repo_path, test_command)
                test_step.complete()

                # Checkpoint after tests
                recovery.save_checkpoint(
                    session_id, cycle, "post_test",
                    {"test_passed": test_result.ok,
                     "test_summary": test_result.summary}
                )

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

                # Resilience: use fallback chain for test review (in case primary agent is down)
                # UI 4: Track review timing
                review_step = cycle_timeline.add_step("test_review", "haiku")
                fallback = get_fallback_chain()
                agent_calls = {
                    "haiku": lambda: haiku.review_after_tests(**review_kwargs),
                    "sonnet": lambda: (
                        sonnet.review_after_tests(
                            **{**review_kwargs,
                               "thinking_budget_tokens": effort_config["thinking_budget_tokens"]}
                        ) if effort_config.get("thinking_budget_tokens") else
                        sonnet.review_after_tests(**review_kwargs)
                    ),
                    "opus": lambda: (
                        opus.review_after_tests(
                            **{**review_kwargs,
                               "thinking_budget_tokens": effort_config["thinking_budget_tokens"]}
                        ) if effort_config.get("thinking_budget_tokens") else
                        opus.review_after_tests(**review_kwargs)
                    ),
                }

                try:
                    review, review_agent_name = fallback.execute_with_fallback(
                        "test_review", agent_calls
                    )
                    review_step.agent = review_agent_name
                    review_step.complete()
                    if review_agent_name != "haiku":
                        # UI 3: Fallback notification
                        logger.info(f"FALLBACK: Test review used {review_agent_name} (primary haiku unavailable)")
                except RuntimeError as e:
                    logger.error(f"All agents failed for test review: {e}")
                    raise
                logger.info(
                    f"Tests run by {test_runner_name}, "
                    f"reviewed by {review_agent_name} ({review_effort.value} effort)"
                )

                # Checkpoint after review
                recovery.save_checkpoint(
                    session_id, cycle, "post_review",
                    {"review": review}
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

                # Complete cycle timeline
                cycle_timeline.complete()
                logger.info(cycle_timeline.format_summary())

                # Clean up checkpoints for completed cycle
                recovery.cleanup_cycle_checkpoints(session_id, cycle)

                if test_result.ok:
                    # Cleanup timeline when session is done
                    cleanup_session_timeline(session_id)
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
                        reason="test_failed_pattern",
                        context={"error": error_msg, "cycle": cycle, "max_cycles": max_cycles})

                # Improvement 3: Smart retry — refine instead of regenerate if actionable
                should_refine, prompt = should_retry_with_refinement(
                    test_result.tail() or "",
                    instructions,
                    cycle - 1  # failure_count
                )

                if should_refine:
                    # Refine existing implementation (cheaper, targeted)
                    logger.info(f"Cycle {cycle}: Smart retry — refining instructions")
                    instructions = prompt
                else:
                    # Regenerate from scratch (full analysis + reimplementation)
                    logger.info(f"Cycle {cycle}: Smart retry — regenerating from scratch")

                    # Resilience: use fallback chain for bug analysis (in case Sonnet is down)
                    fallback = get_fallback_chain()
                    bug_calls = {
                        "sonnet": lambda: sonnet.bug_from_failure(
                            instructions=instructions, test_output=test_result.tail()),
                        "opus": lambda: opus.bug_from_failure(
                            instructions=instructions, test_output=test_result.tail()),
                        "haiku": lambda: haiku.bug_from_failure(
                            instructions=instructions, test_output=test_result.tail()),
                    }

                    try:
                        instructions, bug_agent = fallback.execute_with_fallback(
                            "bug_analysis", bug_calls
                        )
                        if bug_agent != "sonnet":
                            logger.warning(f"Bug analysis degraded: using {bug_agent} instead of sonnet")
                    except RuntimeError as e:
                        logger.error(f"All agents failed for bug analysis: {e}")
                        raise

            except Exception as e:
                # Crash during cycle: save error checkpoint and escalate
                error_str = f"{type(e).__name__}: {str(e)}"
                logger.exception(f"Cycle {cycle} crashed: {error_str}")

                recovery.save_checkpoint(
                    session_id, cycle, "post_review",
                    {"error": error_str},
                    error=error_str
                )

                return self._escalate(
                    session_id, project, tracker,
                    reason="test_crash",
                    context={"cycle": cycle, "error": error_str})

        # Clean up all checkpoints for session after completion
        recovery.cleanup_session_checkpoints(session_id)

        return self._escalate(
            session_id, project, tracker,
            reason="cycle_limit",
            context={"max_cycles": max_cycles, "last_failure": "Test still failing"})

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

    def _select_impl_agent(self, haiku, context: str, plan: str):
        """Choose the implementation agent AND effort for this session.

        When LOCAL_MANAGER_AUTO_ROUTE_IMPL is enabled (default), Haiku judges the
        task on two independent axes: complexity (which model tier) and effort
        (reasoning depth). complexity maps to the configurable roles so routing
        follows whatever agent is assigned to each role:
          low -> validation role, medium -> planning role, high -> implementation.
        The chosen agent leads the fallback chain; effort overrides the coder's
        thinking depth (a complex-but-localized fix can run at lower effort).

        Always safe: if routing is disabled or classification fails, returns the
        configured implementation agent and no effort override (never under-
        powers). Returns (agent_label, effort_or_None).
        """
        from .agents.registry import get_registry
        reg = get_registry()
        configured = reg.get_implementation_agent()

        auto = self._config.get_raw(
            "LOCAL_MANAGER_AUTO_ROUTE_IMPL", "true").lower() == "true"
        if not auto:
            return configured, None

        try:
            complexity, effort, reason = haiku.classify_complexity(context, plan)
            tier_to_agent = {
                "low": reg.get_validation_agent(),
                "medium": reg.get_planning_agent(),
                "high": reg.get_implementation_agent(),
            }
            agent = tier_to_agent.get(complexity, configured)
            logger.info(
                "Implementation routing: complexity=%s -> %s, effort=%s (%s)",
                complexity, agent, effort, reason)
            return agent, effort
        except Exception as e:
            logger.warning(
                "Complexity routing failed (%s); using configured agent '%s'.",
                e, configured)
            return configured, None

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
                  reason: str = "", context: dict = None) -> dict:
        report = self._record_cost(session_id, project, tracker)

        # Improvement 2: Better escalation messages — context-aware reason building
        if context:
            escalation_msg = build_escalation_reason(reason, context)
        else:
            escalation_msg = reason or "Escalation required"

        self._db.update_session(session_id, status="failed",
                                error_message=escalation_msg)
        self._audit.update(session_id, project, {"escalation": {"reason": escalation_msg}})
        self._notifier.notify(
            "escalation",
            title="Escalation — needs you",
            message=f"Session {session_id[:8]} escalated: {escalation_msg[:100]}...",
            email_subject="ZillaSoft Local Manager — escalation",
            email_html=f"<p>{escalation_msg}</p>")
        logger.info("Session %s escalated: %s", session_id, escalation_msg)
        return {"status": "escalated", "reason": escalation_msg, "cost": report.total}

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
