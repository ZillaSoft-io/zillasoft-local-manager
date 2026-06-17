# Phase 2 Integration Guide

This guide shows how to integrate the new orchestrator, concurrent execution, and API clients into the control flow.

## Architecture Overview

```
User request
    ↓
[Haiku: clarify & validate]
    ↓
[Sonnet: generate plan] → cached?
    ↓
[Route: analyze plan] → Haiku or Opus?
    ↓
[Execute: parallel if independent, sequential if dependent]
    ↓
[Cost: track usage per agent]
    ↓
[Store: update DB with cost breakdown]
```

## Integration Points

### 1. Controller Integration (Highest Priority)

**File:** `app/control/controller.py`

Replace the sequential agent calls with the orchestrator:

```python
from app.agents.orchestrator import AgentOrchestrator
from app.cache import SessionCache
from app.database import Database

class Controller:
    def process_task(self, session_id: str, user_input: str) -> dict:
        """Process a task through the full pipeline."""
        
        # Setup
        cache = SessionCache()
        db = Database("./local_manager.db")
        session = db.get_session(session_id)
        
        # Phase 1: Clarification (Haiku)
        # ... existing code ...
        
        # Phase 2: Plan generation (Sonnet)
        orchestrator = AgentOrchestrator(
            haiku=self.haiku_agent,
            sonnet=self.sonnet_agent,
            opus=self.opus_agent,
            cache=cache,
            session_id=session_id,
        )
        
        plan = orchestrator.plan_phase(context)
        if not plan:
            return {"status": "error", "message": "Plan generation failed"}
        
        # Phase 3: Routing & Execution
        agent = orchestrator.routing_phase(plan)
        instructions = sonnet.generate_instructions(context, plan)
        
        # Option A: Sequential (simple)
        result = orchestrator.execution_phase(agent, instructions)
        
        # Option B: Parallel (if instructions split into two independent tasks)
        # simple_task, complex_task = split_instructions(instructions)
        # haiku_result, opus_result = orchestrator.parallel_execution_phase(
        #     simple_task, complex_task
        # )
        
        # Phase 4: Cost tracking
        breakdown = orchestrator.cost_phase()
        
        # Update database
        db.update_session(session_id, 
            cost_breakdown=breakdown.to_dict(),
            total_cost=breakdown.total_cost_usd,
        )
        
        return {
            "status": "success",
            "result": result,
            "cost_usd": breakdown.total_cost_usd,
        }
```

### 2. API Call Integration (Medium Priority)

**Current state:** Direct API calls without circuit breaker protection

**New state:** Wrap calls with circuit breaker

**Example before:**
```python
# Old: no protection
from github import Github
client = Github(token)
client.get_user().create_repo("my-repo")
```

**Example after:**
```python
# New: with circuit breaker
from app.api_client import github

try:
    result = github().create_branch("ZillaSoft-io/Zillasoft", "feature/xyz")
except CircuitBreakerOpen:
    print("GitHub is down, will retry in 60s")
    # Don't burn tokens retrying
```

**Where to integrate:**
- `control/controller.py` — GitHub branch creation, PR creation
- `control/deployer.py` — Railway deployments (if exists)
- Any code calling external APIs

### 3. Session/Database Integration (Medium Priority)

**Current state:** Session stores `cost_breakdown` as JSON column but not populated

**New state:** Populate `cost_breakdown` after each cycle

**In controller after orchestrator:**
```python
# After orchestrator.cost_phase()
breakdown = orchestrator.cost_phase()

# Serialize and store
db.update_session(session_id,
    cost_breakdown=breakdown.to_dict(),  # JSON
    total_cost=breakdown.total_cost_usd,
    total_tokens_used=breakdown.total_tokens,
)

# Log to structured logger
from app.logging_structured import StructuredLogger
logger = StructuredLogger(__name__)
logger.info(
    "Session cost tracking",
    session_id=session_id,
    total_cost_usd=breakdown.total_cost_usd,
    agent_summary=breakdown.agent_summary,
)
```

### 4. File Read Caching (Low Priority, High ROI)

**Where:** Any `read(filepath)` calls in agents or controller

**Current:**
```python
from app.code_reader import CodeReader
reader = CodeReader()
content = reader.read("src/agents/base.py")
```

**New:**
```python
from app.code_reader import CodeReader
from app.cache import SessionCache, cache_file_read, get_cached_file

reader = CodeReader()
cache = SessionCache()

# Check cache first
cached = get_cached_file(cache, "src/agents/base.py")
if cached:
    content = cached
else:
    content = reader.read("src/agents/base.py")
    cache_file_read(cache, "src/agents/base.py", content)
```

---

## Parallel Execution Scenarios

### Scenario 1: Single Task (Simple)
```
Sonnet: "Rename x to user_id"
  ↓ route → Haiku
  ↓ execute → Haiku implements
```

### Scenario 2: Single Task (Complex)
```
Sonnet: "Implement caching layer with Redis"
  ↓ route → Opus
  ↓ execute → Opus implements
```

### Scenario 3: Two Independent Tasks (New!)
```
Sonnet:
  "Task A: Rename variable (simple)"
  "Task B: Add error handling (complex)"
  
  ↓ split & route
  Task A → Haiku   }
  Task B → Opus    } run in parallel
  
  ↓ merge results
  return (haiku_result, opus_result)
```

**How to detect independent tasks:**

```python
from app.agents.routing import analyze_plan

plan = sonnet.generate_dry_run_plan(context)

# Check if plan contains multiple independent sections
# (heuristic: "Task A:" and "Task B:" prefixes)
tasks = plan.split("Task ")
if len(tasks) > 2:  # Found multiple tasks
    instructions_a = f"Task {tasks[1]}"
    instructions_b = f"Task {tasks[2]}"
    
    haiku_result, opus_result = orchestrator.parallel_execution_phase(
        instructions_a, instructions_b
    )
else:
    # Single task: route and execute sequentially
    agent = orchestrator.routing_phase(plan)
    result = orchestrator.execution_phase(agent, plan)
```

---

## Testing Phase 2

### Unit Tests

1. **Test orchestrator phases:**
   ```python
   def test_plan_phase_caching():
       orchestrator = AgentOrchestrator(...)
       plan1 = orchestrator.plan_phase(context)
       plan2 = orchestrator.plan_phase(context)
       assert plan1 == plan2  # cached
   ```

2. **Test routing:**
   ```python
   from app.agents.routing import analyze_plan
   
   simple_plan = "Rename variable x to user_id"
   assert analyze_plan(simple_plan) == RoutingDecision.USE_HAIKU
   
   complex_plan = "Implement new caching layer with Redis"
   assert analyze_plan(complex_plan) == RoutingDecision.USE_OPUS
   ```

3. **Test concurrent execution:**
   ```python
   async def test_parallel_tasks():
       executor = ConcurrentExecutor()
       tasks = [
           TaskSpec(..., fn=fast_fn, ...),
           TaskSpec(..., fn=slow_fn, ...),
       ]
       results = await executor.execute_all(tasks)
       assert all(r.success for r in results)
   ```

4. **Test circuit breaker:**
   ```python
   from app.resilience import CircuitBreakerOpen
   
   breaker = get_breaker("test")
   
   # Trigger 3 failures
   for _ in range(3):
       try:
           breaker.call(failing_fn)
       except:
           pass
   
   # Circuit should be open now
   with pytest.raises(CircuitBreakerOpen):
       breaker.call(passing_fn)
   ```

### Integration Tests

1. **Test full pipeline with caching:**
   - Submit task → plan cached
   - Submit similar task → reuse cached plan
   - Verify tokens saved

2. **Test routing decision:**
   - Submit simple task → routed to Haiku → verify cheaper
   - Submit complex task → routed to Opus → verify expensive

3. **Test parallel execution:**
   - Submit multi-task request
   - Verify both tasks run concurrently (wall-clock time < sum of individual times)

4. **Test circuit breaker:**
   - Mock API failure
   - Verify circuit opens after 3 failures
   - Verify fast rejection
   - Verify recovery after timeout

---

## Rollout Plan

### Week 1: Core Integration
- [ ] Integrate orchestrator into controller
- [ ] Integrate API clients with circuit breaker
- [ ] Write unit tests

### Week 2: Database Integration
- [ ] Connect cost tracking to database
- [ ] Add cost display to UI
- [ ] Write integration tests

### Week 3: Optimization
- [ ] Enable file read caching
- [ ] Enable plan caching
- [ ] Monitor cache hit rates

### Week 4: Parallel Execution
- [ ] Detect multi-task requests
- [ ] Implement task splitting
- [ ] Run parallel execution

---

## Files Modified/Created

**Created:**
- `app/agents/concurrent.py` — concurrent execution
- `app/agents/orchestrator.py` — orchestration logic
- `app/api_client.py` — circuit-breaker-protected API clients
- `PHASE2_INTEGRATION_GUIDE.md` — this file

**To Modify:**
- `app/control/controller.py` — wire in orchestrator
- `app/database.py` — (no changes needed, schema ready)
- UI layer — display cost breakdown

**Already Ready (Phase 1):**
- `app/agents/registry.py` — agent registry
- `app/agents/routing.py` — routing logic
- `app/cache.py` — session caching
- `app/cost/breakdown.py` — cost tracking
- `app/logging_structured.py` — structured logging
- `app/resilience.py` — circuit breaker

---

## Troubleshooting

**Q: "Event loop already running" error**

A: If running in Jupyter or asyncio context, wrap with:
```python
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
```

**Q: Circuit breaker too aggressive (opens too fast)**

A: Adjust in `app/resilience.py`:
```python
breaker = CircuitBreaker(
    name="github",
    failure_threshold=5,      # was 3
    recovery_timeout_secs=120  # was 60
)
```

**Q: Cache hit rate too low**

A: Check if task descriptions are consistent:
```python
# Bad: different every time
plan_key = full_user_input

# Good: normalized key
plan_key = normalize(user_intent_only)
```

---

## Metrics to Monitor

After Phase 2 launch:

1. **Cost savings:**
   - Haiku call count (should increase)
   - Opus call count (should decrease)
   - Average cost per task (should decrease 30-50%)

2. **Cache effectiveness:**
   - Cache hit rate (target: >30%)
   - Tokens saved by caching (estimate: 20-30%)

3. **Resilience:**
   - Circuit breaker trips (target: 0-1/week)
   - API retry storm preventions

4. **Performance:**
   - Wall-clock time for parallel tasks (target: <1.5x fastest component)
   - Plan generation latency (should stay same or improve due to caching)

---

## Next Steps

1. Review this guide with Mario
2. Integrate orchestrator into `controller.py`
3. Add unit tests
4. Deploy Phase 2 on staging
5. Monitor metrics for 1 week
6. Full rollout to production

