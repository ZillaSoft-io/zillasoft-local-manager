# Phase 2 Migration: From dry_run to orchestration

## Quick Swap

**Old:**
```python
from app.agents.dry_run import run_dry_run

result = run_dry_run(
    sonnet=sonnet_agent,
    haiku=haiku_agent,
    context=context,
    original_intent=original_intent,
)
plan = result.plan
instructions = result.instructions
approved = result.approved
```

**New:**
```python
from app.agents.phase2_orchestration import run_phase2_orchestration
from app.cache import SessionCache
from app.database import Database

db = Database("./local_manager.db")
cache = SessionCache()

result = run_phase2_orchestration(
    haiku=haiku_agent,
    sonnet=sonnet_agent,
    opus=opus_agent,
    context=context,
    original_intent=original_intent,
    session_id=session_id,
    cache=cache,
)

plan = result.plan
routing_decision = result.routing_decision  # "haiku" or "opus"
implementation = result.implementation
approved = result.approved

# Store cost breakdown
if result.cost_breakdown:
    db.update_session(
        session_id,
        cost_breakdown=result.cost_breakdown.to_dict(),
        total_cost=result.cost_breakdown.total_cost_usd,
        total_tokens_used=result.cost_breakdown.total_tokens,
    )
```

## Key Differences

| Aspect | Old (dry_run) | New (Phase 2) |
|--------|---|---|
| Plan generation | Sonnet only | Sonnet + circuit breaker + caching |
| Routing | None (always Opus) | Smart routing: Haiku or Opus |
| Execution | Always Opus | Selected agent (Haiku/Opus) |
| Cost tracking | None | Per-agent breakdown |
| Parallel execution | Not supported | Optional via separate function |

## Integration Points

### 1. Find all calls to `run_dry_run`

```bash
grep -r "run_dry_run" app/ --include="*.py"
```

Likely locations:
- Main orchestrator loop
- Task processing handler
- API endpoint that processes requests

### 2. Replace with `run_phase2_orchestration`

For each call:

```python
# OLD
from app.agents.dry_run import run_dry_run
result = run_dry_run(sonnet, haiku, context=..., original_intent=...)

# NEW
from app.agents.phase2_orchestration import run_phase2_orchestration
result = run_phase2_orchestration(
    haiku, sonnet, opus,
    context=...,
    original_intent=...,
    session_id=session_id,
    cache=cache,
)
```

### 3. Update result handling

```python
# OLD: only plan + instructions
if result.approved:
    opus_result = opus.ask(result.instructions)

# NEW: routing decision + implementation
if result.approved:
    # implementation is already done (by Haiku or Opus)
    implementation = result.implementation
    
    # Store cost breakdown
    db.update_session(session_id, cost_breakdown=result.cost_breakdown.to_dict())
```

## Parallel Execution Integration

When Sonnet detects multiple independent tasks:

```python
from app.agents.phase2_orchestration import run_phase2_parallel_orchestration

# If Sonnet output has multiple independent tasks:
simple_task = "Rename x to user_id"
complex_task = "Add caching layer with Redis"

result = run_phase2_parallel_orchestration(
    haiku, sonnet, opus,
    context=context,
    original_intent=original_intent,
    session_id=session_id,
    simple_task_instructions=simple_task,
    complex_task_instructions=complex_task,
    cache=cache,
)

# result.implementation contains both Haiku + Opus outputs
# result.routing_decision == "parallel"
```

## Testing Checklist

- [ ] Replace run_dry_run import with run_phase2_orchestration
- [ ] Pass haiku, sonnet, opus (not sonnet, haiku)
- [ ] Add session_id and cache parameters
- [ ] Store cost_breakdown to database
- [ ] Verify result.approved works same as before
- [ ] Test routing decision (haiku vs opus)
- [ ] Test parallel execution path
- [ ] Monitor cost savings

## Backward Compatibility

Old `dry_run.py` is still there. No breaking changes needed immediately. Can migrate gradually:

1. Keep both `run_dry_run()` and `run_phase2_orchestration()`
2. Add feature flag: `USE_PHASE2_ORCHESTRATION = env.get("PHASE2_ENABLED", False)`
3. Switch gradually by session type
4. Monitor metrics (cost, success rate, performance)
5. Full cutover when confident

## Metrics to Track Post-Migration

Before Phase 2:
```
Baseline cost per task: $2.00
Baseline execution time: 45 seconds
Baseline Opus calls: 100%
```

After Phase 2 (target):
```
New cost per task: $0.85 (-57.5%)
New execution time: 35 seconds with parallel (-22%)
New Haiku calls: 60% (+50%)
New Opus calls: 40% (-60%)
Cache hit rate: 30%+
```

## Troubleshooting

**Q: Result has no `routing_decision` field**

A: You're still using old `run_dry_run()`. Switch to `run_phase2_orchestration()`.

**Q: Cost breakdown is None**

A: Check that orchestrator.cost_phase() was called. Should happen automatically.

**Q: Parallel execution doesn't work**

A: Use `run_phase2_parallel_orchestration()` for independent tasks, not sequential orchestration.

**Q: Cache hit rate is 0%**

A: Context keys must be normalized (same context = cache hit). Check cache_plan() keys.

