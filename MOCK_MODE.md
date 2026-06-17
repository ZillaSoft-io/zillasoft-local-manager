# Mock Mode: Test Full Pipeline at $0 Cost

**Option 2: Mock + Replay** allows you to run the entire orchestration pipeline without hitting the Claude API.

## What It Does

- Replays recorded API responses from past sessions
- Simulates realistic latency (~500ms-8.5s per call, with ±20% jitter)
- No API tokens consumed, no cost
- Tests UI, fallback chains, error handling, timelines, cost tracking
- Useful for CI/CD, regression testing, and manual testing

## Quick Start

### Run Test Script (Instant)

```bash
python test_mock_run.py
```

Expected output:
- Direct agent calls (haiku, sonnet, opus)
- Config-based agent factory
- Fallback chain simulation
- All tests pass in ~15 seconds

### Enable Mock Mode in Full App

```bash
# Via environment variable
export LOCAL_MANAGER_MOCK_MODE=true
python -m app.main

# Via .env file
echo "LOCAL_MANAGER_MOCK_MODE=true" >> .env
python -m app.main
```

The app will show:
```
⚠️  MOCK MODE ENABLED — Using recorded responses, no API calls
✓ Haiku agent ready
✓ Sonnet agent ready
✓ Opus agent ready
```

## Configuration

Three environment variables control mock mode:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOCAL_MANAGER_MOCK_MODE` | `false` | Enable/disable mock agents |
| `MOCK_SESSION_ID` | `demo` | Which recorded session to replay |
| `MOCK_ENABLE_LATENCY` | `true` | Simulate realistic latency (or run instantly) |

Examples:

```bash
# Run fast (no latency)
MOCK_ENABLE_LATENCY=false python -m app.main

# Use custom recorded session
MOCK_SESSION_ID=my_session python -m app.main

# Full feature test with latency
LOCAL_MANAGER_MOCK_MODE=true MOCK_SESSION_ID=demo python -m app.main
```

## Recording Your Own Sessions

To record real API responses for playback:

```python
from app.mock_agents import ResponseRecorder
from app.agents import build_agents
from app.config import ConfigHandler

config = ConfigHandler()
recorder = ResponseRecorder()
recorder.start_session("my_test_session")

# Build real agents
haiku, sonnet, opus, tracker = build_agents(config, mock_mode=False)

# Run a session...
# Each API call is automatically recorded

# Save recordings to disk
recorder.save_session()
# Creates: .recordings/my_test_session_responses.json
```

Then replay:
```bash
MOCK_SESSION_ID=my_test_session python -m app.main
```

## What Gets Tested

With mock mode enabled, you can test:

✓ **Full orchestration loop**
- Plan generation (Sonnet) → Validation (Haiku) → Implementation (Opus) → Testing → Review

✓ **Fallback chains**
- Trigger degradation scenarios (simulate agent unavailability)
- Verify fallback behavior (Sonnet → Haiku, Opus → Sonnet, etc.)
- Test health tracking and recovery

✓ **UI features**
- Agent health badges (degraded/healthy status)
- Cycle timeline (timing breakdown per step)
- Cost tracking (all zeros in mock mode, but structure tested)
- Fallback notifications

✓ **System resilience**
- Rate limit backoff (jittered exponential backoff)
- Request timeouts
- Crash recovery checkpoints
- Health monitoring

✓ **Performance**
- Latency simulation (realistic delays)
- Throughput testing (run 50 cycles in seconds)
- Timing validation

## Recorded Session Format

Mock responses are stored as JSON:

```json
[
  {
    "timestamp": "2026-01-15T10:30:00Z",
    "model": "claude-3-5-sonnet-20241022",
    "prompt_hash": "abc123",
    "response_text": "Plan text here...",
    "tokens_input": 2500,
    "tokens_output": 350,
    "latency_ms": 2300.0
  },
  ...
]
```

Stored in: `.recordings/{session_id}_responses.json`

## Use Cases

### CI/CD Testing

```bash
# .github/workflows/test.yml
env:
  LOCAL_MANAGER_MOCK_MODE: true
  MOCK_ENABLE_LATENCY: false
run: pytest tests/integration/
```

No API key needed, tests run in seconds, no cost.

### Manual Testing

```bash
# Test a new feature
MOCK_SESSION_ID=demo python -m app.main
# Navigate UI, test fallback scenarios, verify timelines
```

### Performance Regression Testing

```bash
# Measure orchestration speed
MOCK_ENABLE_LATENCY=true python -m app.main
# Full pipeline with realistic latency simulation
```

### Stress Testing

```python
# Run 100 sessions in parallel
from app.mock_agents import create_mock_agents
from app.agents import build_agents

for i in range(100):
    haiku, sonnet, opus, _ = create_mock_agents(
        session_id="demo",
        enable_latency=False  # Fast for stress test
    )
    # Run session...
```

## Latency Simulation

Mock agents add realistic delays:

| Agent | Task | Latency |
|-------|------|---------|
| Sonnet | Plan generation | ~2.3s |
| Haiku | Plan validation | ~0.5s |
| Opus | Implementation | ~8.5s |
| Haiku | Test review | ~0.7s |

Each call adds ±20% jitter to simulate real variance.

Disable latency for instant testing:

```bash
MOCK_ENABLE_LATENCY=false python -m app.main  # No delays, runs in <1s
```

## Example: Testing Degradation

```python
# Simulate Opus unavailability
from app.agent_fallback import get_fallback_chain

fallback = get_fallback_chain()
fallback.health["opus"].record_failure()
fallback.health["opus"].record_failure()
# Opus now marked degraded

# Run pipeline
# → Implementation falls back to Sonnet (not Opus)
# → Logged: "FALLBACK: Implementation used sonnet (primary opus unavailable)"
```

## Limitations

What mock mode **does not** test:

- Actual Claude model intelligence (uses canned responses)
- Real code execution (mock bash output)
- Live git operations (mock commits)
- Actual error recovery (playback only, no real failures)

For those, use real mode with actual API keys.

## Switching Back to Real Mode

```bash
unset LOCAL_MANAGER_MOCK_MODE
python -m app.main  # Uses real API
```

Or remove from `.env`:

```bash
# .env
# LOCAL_MANAGER_MOCK_MODE=true  # <-- commented out
```

---

**Summary**: Mock mode lets you test the full system end-to-end without API costs. Perfect for CI/CD, UI testing, and regression testing.
