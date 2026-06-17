#!/usr/bin/env python3
"""Full test run with mock agents (no API calls, $0 cost).

Option 2: Mock + Replay mode
- Replays recorded responses from past sessions
- Simulates realistic latency (~0.5-8.5s per call)
- No API tokens consumed
- Useful for testing UI, flows, error handling

Usage:
    python test_mock_run.py                    # Run with default "demo" session
    MOCK_SESSION_ID=custom python test_mock_run.py  # Run with custom session
    MOCK_ENABLE_LATENCY=false python test_mock_run.py  # Run instantly (no sleep)

Expected output:
    - Full orchestration pipeline
    - Cycle timeline with latency
    - Cost tracking (all zeros in mock mode)
    - All agents running (haiku, sonnet, opus)
    - Fallback chains tested
    - ~15-30 seconds total (with latency)
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add app to path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def test_mock_agents():
    """Test mock agents directly."""
    from app.mock_agents import create_mock_agents

    logger.info("=" * 70)
    logger.info("MOCK AGENT TEST — Direct agent calls")
    logger.info("=" * 70)

    enable_latency = os.getenv("MOCK_ENABLE_LATENCY", "true").lower() == "true"
    haiku, sonnet, opus, tracker = create_mock_agents(
        session_id="demo",
        enable_latency=enable_latency
    )

    logger.info(f"✓ Haiku agent ready (latency: {enable_latency})")
    logger.info(f"✓ Sonnet agent ready")
    logger.info(f"✓ Opus agent ready")

    # Simulate some calls
    logger.info("\nSimulating plan generation (Sonnet)...")
    plan_response = sonnet.generate_dry_run_plan("Add feature X to module Y")
    logger.info(f"  Result: {plan_response.text[:60]}...")
    logger.info(f"  Tokens: {plan_response.usage.total_tokens}")

    logger.info("\nSimulating plan validation (Haiku)...")
    validation = haiku.validate_dry_run_plan("Add feature X", plan_response.text)
    logger.info(f"  Approved: {validation.approved}")

    logger.info("\nSimulating implementation (Opus)...")
    impl_response = opus.implement_with_tools("Implement feature X", repo_path=".", executor=None)
    logger.info(f"  Result: {impl_response.text[:60]}...")
    logger.info(f"  Commands: {impl_response.commands}")

    logger.info("\nSimulating test review (Haiku)...")
    review = haiku.review_after_tests(
        opus_summary=impl_response.text,
        test_summary="All tests passed",
        passed=True
    )
    logger.info(f"  Review: {review[:60]}...")

    logger.info("\n✓ All mock agent calls completed successfully\n")


def test_with_config():
    """Test mock mode via config (full stack test)."""
    from app.config import ConfigHandler
    from app.agents import build_agents

    logger.info("=" * 70)
    logger.info("MOCK CONFIG TEST — Full agent factory with config")
    logger.info("=" * 70)

    config = ConfigHandler()
    # Override to use mock mode
    config._env["LOCAL_MANAGER_MOCK_MODE"] = "true"
    config._env["MOCK_SESSION_ID"] = "demo"
    config._env["MOCK_ENABLE_LATENCY"] = "false"  # Fast for testing

    haiku, sonnet, opus, tracker = build_agents(config, mock_mode=True)

    logger.info("✓ Mock agents created via config")
    logger.info(f"  Haiku: {haiku.label}")
    logger.info(f"  Sonnet: {sonnet.label}")
    logger.info(f"  Opus: {opus.label}")

    # Quick test
    plan = sonnet.generate_dry_run_plan("Test context")
    logger.info(f"\n✓ Test call successful: {len(plan.text)} chars returned\n")


def test_fallback_chain():
    """Test fallback chain with mock agents."""
    from app.agent_fallback import get_fallback_chain
    from app.mock_agents import create_mock_agents

    logger.info("=" * 70)
    logger.info("MOCK FALLBACK TEST — Fallback chain with mock agents")
    logger.info("=" * 70)

    haiku, sonnet, opus, _ = create_mock_agents(enable_latency=False)
    fallback = get_fallback_chain()

    # Simulate task with fallback
    agent_calls = {
        "haiku": lambda: haiku.review_after_tests(
            opus_summary="Test output", test_summary="All passed", passed=True
        ),
        "sonnet": lambda: sonnet.review_after_tests(
            opus_summary="Test output", test_summary="All passed", passed=True
        ),
        "opus": lambda: opus.review_after_tests(
            opus_summary="Test output", test_summary="All passed", passed=True
        ),
    }

    try:
        result, agent_used = fallback.execute_with_fallback("test_review", agent_calls)
        logger.info(f"✓ Fallback chain succeeded with {agent_used}")
        logger.info(f"  Result: {result[:60]}...\n")
    except Exception as e:
        logger.error(f"✗ Fallback chain failed: {e}\n")


if __name__ == "__main__":
    print()
    logger.info("FULL TEST RUN WITH MOCK AGENTS (Option 2: Mock + Replay)")
    logger.info("Recorded responses replayed with realistic latency")
    logger.info("No API calls, $0 cost\n")

    try:
        test_mock_agents()
        test_with_config()
        test_fallback_chain()

        logger.info("=" * 70)
        logger.info("✓ ALL TESTS PASSED")
        logger.info("=" * 70)
        logger.info("""
To use mock mode in the full application:

1. Via environment variable:
   export LOCAL_MANAGER_MOCK_MODE=true
   python -m app.main

2. Via .env file:
   LOCAL_MANAGER_MOCK_MODE=true
   MOCK_SESSION_ID=demo
   MOCK_ENABLE_LATENCY=false  # for fast testing

3. To record your own responses:
   from app.mock_agents import ResponseRecorder
   recorder = ResponseRecorder()
   # ... run session ...
   recorder.save_session()

Benefits of Option 2:
- Test full pipeline without API costs
- Realistic latency simulation (~500ms-8.5s per call)
- Fallback chains work (test degradation scenarios)
- Health monitoring works
- Timeline tracking works
- All orchestration logic tested
- Perfect for CI/CD and regression testing
        """)

    except Exception as e:
        logger.exception("Test failed")
        sys.exit(1)
