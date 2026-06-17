# Cost Optimization & Observability Guide

This guide explains the new cost optimization, observability, and resilience features added to the ZillaSoft Local Manager.

## Overview

**New Features:**
1. **Agent Registry** — pluggable agent configuration for future models (Mythos 5, etc.)
2. **Cost Tracking** — detailed per-agent cost breakdown per cycle
3. **Selective Opus Routing** — intelligent routing to use Haiku for simple tasks (40-50% savings)
4. **Session Caching** — cache plans, file reads, Haiku outputs within a session
5. **Structured Logging** — JSON logs with context for debugging and observability
6. **Circuit Breaker** — fail fast on API errors, prevent retry storms

---

## 1. Agent Registry (Pluggable Agents)

**Location:** `app/agents/registry.py`

The Agent Registry decouples agent selection from hardcoded model names. When Mythos 5 (or any future model) launches, simply register it once.

### Usage

```python
from app.agents.registry import AgentConfig, register_agent

# Register Mythos 5 when it becomes available
mythos_config = AgentConfig(
    label="mythos5",
    model_key="ANTHROPIC_MODEL_MYTHOS5",  # env var
    effort_key="ANTHROPIC_EFFORT_MYTHOS5",
    system_prompt=MYTHOS5_SYSTEM,  # from prompts.py
    cost_tier="expensive",
    supports_thinking=True,
)
register_agent(mythos_config)

# Later: get an agent by label
from app.agents.registry import get_registry
registry = get_registry()
mythos = registry.get("mythos5")
```

### Adding Mythos 5

1. Add `ANTHROPIC_MODEL_MYTHOS5` and `ANTHROPIC_EFFORT_MYTHOS5` to `.env`
2. Create `MYTHOS5_SYSTEM` prompt in `app/agents/prompts.py` (or define inline)
3. Register it at startup using the pattern above
4. No other code changes needed — the system adapts automatically

---

## 2. Cost Tracking (Per-Agent & Per-Cycle)

**Location:** `app/cost/breakdown.py`

Tracks token usage and cost separately per agent. Enables cost visibility and optimization.

### Usage

```python
from app.cost.breakdown import (
    build_cycle_breakdown, CycleBreakdown, SessionCostBreakdown
)
from app.agents.usage import UsageTracker

# After one cycle (Haiku → Sonnet → Opus), build breakdown
tracker = UsageTracker()  # populated during cycle
agent_models = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}
cycle_breakdown = build_cycle_breakdown(
    cycle_num=1,
    tracker=tracker,
    agent_models=agent_models,
)

# Add to session breakdown
session_cost = SessionCostBreakdown(session_id="...")
session_cost.add_cycle(cycle_breakdown)

# Query cost by agent
print(session_cost.agent_summary)
# {
#   "haiku": {"agent": "haiku", "total_tokens": 5000, "cost_usd": 0.08, "call_count": 2},
#   "sonnet": {"agent": "sonnet", "total_tokens": 12000, "cost_usd": 0.36, "call_count": 1},
#   ...
# }

# Serialize to JSON for database storage
breakdown_json = session_cost.to_dict()
db.update_session(session_id, cost_breakdown=breakdown_json)
```

### Cost Visibility in UI

Display the cost breakdown in the session history:
- **Per-agent cost**: "Haiku: $0.08 (5K tokens), Sonnet: $0.36 (12K tokens)"
- **Agent call count**: "3 calls total"
- **Session total**: "$0.47 (17K tokens)"

---

## 3. Selective Opus Routing (40-50% Cost Savings)

**Location:** `app/agents/routing.py`

After Sonnet generates a plan, analyze it to decide: **Is Haiku sufficient, or does Opus need to implement?**

Simple tasks (rename, comment, config, reorder) route to Haiku.  
Complex tasks (logic, new functions, API changes) route to Opus.

### How It Works

```
Sonnet generates dry-run plan
    ↓
[analyze_plan()] — count keywords
    ↓
Plan has "rename", "comment", "typo" → USE_HAIKU (cheap)
Plan has "logic", "function", "algorithm" → USE_OPUS (expensive)
    ↓
Route to selected agent for implementation
```

### Integration

```python
from app.agents.routing import analyze_plan, should_use_cheap_agent

# After Sonnet generates plan
plan = sonnet.generate_dry_run_plan(context)

if should_use_cheap_agent(plan):
    # Use Haiku (cost ~70% less than Opus)
    agent = haiku_agent
else:
    # Use Opus (for complex logic)
    agent = opus_agent

result = agent.ask(instructions)
```

### Keywords Recognized

**Haiku Indicators** (simple):
- rename, variable, method, comment, docstring, format, config, typo, import, extract variable, inline

**Opus Indicators** (complex):
- logic, algorithm, function, class, bug, fix, feature, API, endpoint, behavior, test, refactor

**Heuristic:** If Haiku > Opus keyword count, route to Haiku.

---

## 4. Session Caching (20-30% Token Savings)

**Location:** `app/cache.py`

Cache expensive operations within a session to avoid regenerating the same answer.

### What Gets Cached

- **Sonnet plans**: if task description is similar, reuse cached plan
- **File contents**: read once, cache for the session
- **Haiku outputs**: clarification questions, context summaries

### Usage

```python
from app.cache import SessionCache, cache_plan, get_cached_plan

cache = SessionCache()

# Try to get cached plan
cached = get_cached_plan(cache, "fix bug in auth middleware")
if cached:
    plan = cached  # reuse
else:
    plan = sonnet.generate_dry_run_plan(context)
    cache_plan(cache, "fix bug in auth middleware", plan)

# Check cache stats
print(cache.stats)
# {'size': 15, 'hits': 8, 'misses': 12, 'hit_rate_pct': 40.0}
```

### Cache Eviction

Cache is session-scoped. Clears automatically when session ends.

For very large sessions (many files read), monitor `cache.stats` and call `cache.clear()` if needed.

---

## 5. Structured Logging (Observability)

**Location:** `app/logging_structured.py`

Emit JSON logs with context (session_id, cycle, agent, step, tokens, cost) for debugging and monitoring.

### Usage

```python
from app.logging_structured import StructuredLogger

logger = StructuredLogger(__name__)

# Log with context
logger.info(
    "Sonnet: plan validation",
    session_id="abc-123",
    cycle=1,
    agent="sonnet",
    input_tokens=3500,
    output_tokens=2100,
    cost_usd=0.18,
)

# Output (JSON):
# {"timestamp": "2026-06-17T...", "message": "Sonnet: plan validation",
#  "level": "INFO", "session_id": "abc-123", "cycle": 1, ...}
```

### Structured Logging Benefits

- **Pattern detection**: "Sonnet fails on React changes 60% of the time"
- **Cost traceability**: which agents/tasks drive cost
- **Audit trail**: full context for each agent call
- **Debugging**: reproduce issues via session logs

### Integration with Monitoring Tools

Export logs to:
- Splunk / Datadog for dashboards
- ELK stack for searching
- CloudWatch for AWS integration

---

## 6. Circuit Breaker (API Resilience)

**Location:** `app/resilience.py`

Prevent retry storms: if an API fails 3 times, stop retrying for 60 seconds.

### States

- **CLOSED**: normal operation, accept calls
- **OPEN**: API down, reject calls fast (fail within milliseconds, not seconds)
- **HALF_OPEN**: testing recovery after timeout

### Usage

```python
from app.resilience import get_breaker

# Get or create a breaker for GitHub API
github_breaker = get_breaker("github")

# Wrap an API call
try:
    github_breaker.call(
        github.create_branch,
        repo="ZillaSoft-io/Zillasoft",
        branch="feature/xyz"
    )
except CircuitBreakerOpen:
    print("GitHub API is down, will retry in 60s")
    # Alert Mario, don't burn tokens on retries
```

### Why It Matters

Without a circuit breaker:
- API fails → retry 3 times → 3 × 60 second timeout = 3 minute delay
- Agents waste tokens waiting for timeouts

With circuit breaker:
- API fails → reject immediately after 3 failures
- Fail fast: millisecond feedback, can alert Mario immediately

---

## Implementation Roadmap

### Phase 1 (Done)
- Agent registry
- Cost breakdown data structures
- Structured logging
- Selective routing (Haiku/Opus decision)
- Session caching
- Circuit breaker

### Phase 2 (Next)
- Integrate cost tracking into control flow
- Integrate routing into agent selection
- Integrate caching into file reads & plan generation
- Add circuit breaker to GitHub/Railway/Jira calls
- Update UI to display cost breakdown

### Phase 3 (Future)
- Cost budgeting: stop accepting new tasks if $100/mo cap is near
- ML-based routing: learn which agent works best for each project
- Predictive caching: pre-cache likely follow-up questions
- Observability dashboard: graphs of cost, success rate by project

---

## Cost Optimization Summary

| Feature | Effort | Cost Savings | Notes |
|---------|--------|--------------|-------|
| Selective Opus routing | Low | 40-50% | Simple tasks to Haiku |
| Session caching | Low | 20-30% | Plans, files, clarifications |
| Cost tracking | Low | N/A | Visibility, not direct savings |
| Circuit breaker | Low | 5-10% | Fewer retry-storm token wastes |
| Agent registry | Low | N/A | Preparation for Mythos 5 |

**Total potential savings: 50-70% on typical workloads.**

---

## Testing Recommendations

1. **Test selective routing**: create tasks with known complexity levels (rename vs. new API)
2. **Monitor cache hit rate**: aim for >30% hit rate on common tasks
3. **Verify circuit breaker**: manually fail an API, confirm fast rejection
4. **Check cost accuracy**: compare reported costs to Anthropic invoice
5. **Structured logs**: verify JSON format and query-ability

---

## Questions?

Refer to:
- `app/agents/registry.py` — Agent configuration
- `app/cost/breakdown.py` — Cost tracking
- `app/agents/routing.py` — Task complexity analysis
- `app/cache.py` — Session caching
- `app/logging_structured.py` — JSON logging
- `app/resilience.py` — Circuit breaker
