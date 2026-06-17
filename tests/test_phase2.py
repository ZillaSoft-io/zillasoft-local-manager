"""Phase 2 integration tests."""
import pytest
from app.agents.phase2_orchestration import run_phase2_orchestration
from app.agents.routing import analyze_plan, should_use_cheap_agent
from app.cache import SessionCache
from app.resilience import CircuitBreaker, CircuitBreakerOpen


class MockAgent:
    """Mock agent for testing."""
    def __init__(self, label, model):
        self.label = label
        self.model = model
        self.call_count = 0

    def ask(self, prompt):
        self.call_count += 1
        from unittest.mock import Mock
        response = Mock()
        response.text = f"{self.label} response to: {prompt[:50]}"
        response.usage = Mock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        response.usage.cache_creation_input_tokens = 0
        response.usage.cache_read_input_tokens = 0
        response.usage.total_input = 100
        return response

    def generate_dry_run_plan(self, context):
        from unittest.mock import Mock
        response = Mock()
        response.text = "Plan: rename variable x to user_id"
        response.usage = Mock()
        response.usage.input_tokens = 200
        response.usage.output_tokens = 80
        response.usage.cache_creation_input_tokens = 0
        response.usage.cache_read_input_tokens = 0
        response.usage.total_input = 200
        return response

    def validate_dry_run_plan(self, intent, plan):
        from app.agents.haiku import ValidationVerdict
        return ValidationVerdict(approved=True, corrections="")

    def revise_dry_run_plan(self, context, plan, corrections):
        return plan

    def generate_instructions(self, context, plan):
        return f"Instructions based on: {plan[:50]}"


def test_routing_simple():
    """Simple tasks route to Haiku."""
    plan = "Rename variable x to user_id in main.py"
    assert should_use_cheap_agent(plan) is True


def test_routing_complex():
    """Complex tasks route to Opus."""
    plan = "Implement new caching layer with Redis integration and error handling"
    assert should_use_cheap_agent(plan) is False


def test_circuit_breaker_basic():
    """Circuit breaker opens after 3 failures."""
    breaker = CircuitBreaker("test", failure_threshold=3)

    def failing_fn():
        raise Exception("API error")

    # 3 failures
    for _ in range(3):
        with pytest.raises(Exception):
            breaker.call(failing_fn)

    # Circuit should be open now
    with pytest.raises(CircuitBreakerOpen):
        breaker.call(lambda: "success")


def test_circuit_breaker_recovery():
    """Circuit recovers after timeout."""
    import time
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout_secs=1)

    def failing_fn():
        raise Exception("API error")

    # Trigger 2 failures to open
    for _ in range(2):
        with pytest.raises(Exception):
            breaker.call(failing_fn)

    assert breaker.state.value == "open"

    # Wait for recovery timeout
    time.sleep(1.1)

    # Next call should transition to HALF_OPEN and test recovery
    def success_fn():
        return "success"

    result = breaker.call(success_fn)
    assert result == "success"
    assert breaker.state.value == "closed"


def test_cache_hit():
    """Cache returns stored value."""
    cache = SessionCache()
    cache.set("test_key", "test_value")
    assert cache.get("test_key") == "test_value"


def test_cache_miss():
    """Cache returns None on miss."""
    cache = SessionCache()
    assert cache.get("nonexistent") is None


def test_cache_stats():
    """Cache tracks hits and misses."""
    cache = SessionCache()
    cache.set("key1", "value1")

    # Hit
    cache.get("key1")
    # Miss
    cache.get("key2")

    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate_pct"] == 50.0


def test_phase2_orchestration_basic():
    """Phase 2 orchestration completes successfully."""
    haiku = MockAgent("haiku", "claude-haiku-4-5")
    sonnet = MockAgent("sonnet", "claude-sonnet-4-6")
    opus = MockAgent("opus", "claude-opus-4-8")

    result = run_phase2_orchestration(
        haiku=haiku,
        sonnet=sonnet,
        opus=opus,
        context="Fix bug in auth",
        original_intent="Fix bug in auth",
        session_id="test-session",
        cache=SessionCache(),
    )

    assert result.approved is True
    assert result.success is True
    assert result.cost_breakdown is not None
    assert result.cost_breakdown.total_cost_usd > 0


def test_phase2_routing_decision():
    """Phase 2 makes correct routing decision."""
    haiku = MockAgent("haiku", "claude-haiku-4-5")
    sonnet = MockAgent("sonnet", "claude-sonnet-4-6")
    opus = MockAgent("opus", "claude-opus-4-8")

    # Override sonnet to return simple plan
    original_plan = sonnet.generate_dry_run_plan
    def simple_plan_fn(context):
        from unittest.mock import Mock
        response = Mock()
        response.text = "Rename x to user_id"
        response.usage = Mock()
        response.usage.input_tokens = 100
        response.usage.output_tokens = 50
        response.usage.cache_creation_input_tokens = 0
        response.usage.cache_read_input_tokens = 0
        response.usage.total_input = 100
        return response
    sonnet.generate_dry_run_plan = simple_plan_fn

    result = run_phase2_orchestration(
        haiku=haiku,
        sonnet=sonnet,
        opus=opus,
        context="Rename variable",
        original_intent="Rename variable",
        session_id="test-session",
        cache=SessionCache(),
    )

    # Simple task should route to haiku
    assert result.routing_decision == "haiku"

    sonnet.generate_dry_run_plan = original_plan


def test_cost_tracking():
    """Phase 2 tracks cost per agent."""
    haiku = MockAgent("haiku", "claude-haiku-4-5")
    sonnet = MockAgent("sonnet", "claude-sonnet-4-6")
    opus = MockAgent("opus", "claude-opus-4-8")

    result = run_phase2_orchestration(
        haiku=haiku,
        sonnet=sonnet,
        opus=opus,
        context="Test",
        original_intent="Test",
        session_id="test-session",
        cache=SessionCache(),
    )

    assert result.cost_breakdown is not None
    assert result.cost_breakdown.total_tokens > 0
    assert result.cost_breakdown.total_cost_usd > 0

    # Should have costs for multiple agents
    agents = [ac.agent_label for ac in result.cost_breakdown.agents]
    assert "sonnet" in agents  # Plan generation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
