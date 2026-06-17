"""Phase 2 simple validation tests (no pytest required)."""
from app.agents.routing import analyze_plan, should_use_cheap_agent, RoutingDecision
from app.cache import SessionCache
from app.resilience import CircuitBreaker, CircuitBreakerOpen


def test_routing_simple():
    """Simple tasks route to Haiku."""
    plan = "Rename variable x to user_id in main.py"
    assert should_use_cheap_agent(plan) is True
    print("✓ Routing: simple task → Haiku")


def test_routing_complex():
    """Complex tasks route to Opus."""
    plan = "Implement new caching layer with Redis and error handling for production use"
    assert should_use_cheap_agent(plan) is False
    print("✓ Routing: complex task → Opus")


def test_cache_hit():
    """Cache returns stored value."""
    cache = SessionCache()
    cache.set("test_key", "test_value")
    assert cache.get("test_key") == "test_value"
    print("✓ Cache: hit")


def test_cache_miss():
    """Cache returns None on miss."""
    cache = SessionCache()
    assert cache.get("nonexistent") is None
    print("✓ Cache: miss")


def test_cache_stats():
    """Cache tracks hits and misses."""
    cache = SessionCache()
    cache.set("key1", "value1")
    cache.get("key1")  # hit
    cache.get("key2")  # miss

    stats = cache.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_rate_pct"] == 50.0
    print("✓ Cache: stats tracking")


def test_circuit_breaker_open():
    """Circuit breaker opens after failures."""
    breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout_secs=60)

    def failing_fn():
        raise Exception("API error")

    # Trigger 2 failures to open
    for _ in range(2):
        try:
            breaker.call(failing_fn)
        except Exception:
            pass

    assert breaker.state.value == "open"

    # Circuit should reject new calls
    try:
        breaker.call(lambda: "success")
        assert False, "Should have raised CircuitBreakerOpen"
    except CircuitBreakerOpen:
        print("✓ Circuit breaker: opens on failures")


def test_circuit_breaker_closed():
    """Circuit breaker starts closed."""
    breaker = CircuitBreaker("test")

    def success_fn():
        return "success"

    result = breaker.call(success_fn)
    assert result == "success"
    assert breaker.state.value == "closed"
    print("✓ Circuit breaker: closed on success")


def test_routing_analysis():
    """Routing analysis with multiple keywords."""
    # Haiku indicators only
    plan1 = "Add a docstring and rename the variable from x to user_id"
    assert analyze_plan(plan1) == RoutingDecision.USE_HAIKU
    print("✓ Routing: multi-keyword Haiku task")

    # Opus indicators
    plan2 = "Implement a new caching algorithm with custom data structures and logic"
    assert analyze_plan(plan2) == RoutingDecision.USE_OPUS
    print("✓ Routing: multi-keyword Opus task")


if __name__ == "__main__":
    print("Running Phase 2 validation tests...\n")

    try:
        test_routing_simple()
        test_routing_complex()
        test_cache_hit()
        test_cache_miss()
        test_cache_stats()
        test_circuit_breaker_open()
        test_circuit_breaker_closed()
        test_routing_analysis()

        print("\n✅ All Phase 2 tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        raise
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
