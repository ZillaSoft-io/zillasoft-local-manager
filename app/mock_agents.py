"""Mock agents for testing: replay recorded responses with realistic latency.

Option 2: Mock + Replay mode allows full pipeline testing without API costs.
- Records real responses from sessions
- Replays them in test mode with configurable latency
- Enables realistic performance testing at $0 cost
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class RecordedResponse:
    """A recorded API response for replay."""
    timestamp: str
    model: str
    prompt_hash: str  # hash of prompt for matching
    response_text: str
    tokens_input: int
    tokens_output: int
    latency_ms: float


class ResponseRecorder:
    """Record real API responses for later replay."""

    def __init__(self, recordings_dir: str | Path = ".recordings"):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(exist_ok=True)
        self.session_id: Optional[str] = None
        self.responses: list[RecordedResponse] = []

    def start_session(self, session_id: str) -> None:
        """Start recording a session."""
        self.session_id = session_id
        self.responses = []
        logger.info(f"Recording session {session_id}")

    def record_response(
        self,
        model: str,
        prompt: str,
        response_text: str,
        tokens_input: int,
        tokens_output: int,
        latency_ms: float,
    ) -> None:
        """Record an API response."""
        import hashlib

        prompt_hash = hashlib.md5(prompt[:500].encode()).hexdigest()

        recorded = RecordedResponse(
            timestamp=datetime.now(timezone.utc).isoformat(),
            model=model,
            prompt_hash=prompt_hash,
            response_text=response_text,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            latency_ms=latency_ms,
        )
        self.responses.append(recorded)

    def save_session(self) -> Path:
        """Save recorded session to disk."""
        if not self.session_id:
            raise ValueError("No session started")

        filename = f"{self.session_id}_responses.json"
        filepath = self.recordings_dir / filename

        data = [
            {
                "timestamp": r.timestamp,
                "model": r.model,
                "prompt_hash": r.prompt_hash,
                "response_text": r.response_text,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "latency_ms": r.latency_ms,
            }
            for r in self.responses
        ]

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved {len(self.responses)} recorded responses to {filepath}")
        return filepath


class MockAgentResponse:
    """Mimics AgentResponse from real agents."""

    def __init__(
        self,
        text: str,
        model: str,
        tokens_input: int = 100,
        tokens_output: int = 200,
        latency_ms: float = 500.0,
    ):
        self.text = text
        self.model = model
        self.usage = type("Usage", (), {
            "input_tokens": tokens_input,
            "output_tokens": tokens_output,
            "total_input": tokens_input,
            "total_output": tokens_output,
            "total_tokens": tokens_input + tokens_output,
        })()
        self.stop_reason = "end_turn"
        self.raw = None
        self.latency_ms = latency_ms


class MockAgent:
    """Mock agent that replays recorded responses with realistic latency."""

    def __init__(
        self,
        label: str,
        model: str,
        responses: list[RecordedResponse],
        enable_latency: bool = True,
        latency_jitter: float = 0.2,  # 20% jitter
    ):
        self.label = label
        self.model = model
        self.responses = responses
        self.enable_latency = enable_latency
        self.latency_jitter = latency_jitter
        self.call_index = 0

    def _get_next_response(self) -> RecordedResponse:
        """Get next recorded response (round-robin)."""
        if not self.responses:
            # Fallback: return a generic response
            return RecordedResponse(
                timestamp=datetime.now(timezone.utc).isoformat(),
                model=self.model,
                prompt_hash="",
                response_text="Mock response",
                tokens_input=100,
                tokens_output=200,
                latency_ms=500.0,
            )

        response = self.responses[self.call_index % len(self.responses)]
        self.call_index += 1
        return response

    def _apply_latency(self, latency_ms: float) -> None:
        """Apply realistic latency with jitter."""
        if not self.enable_latency:
            return

        # Add jitter: ±20% of latency
        jitter = random.uniform(1 - self.latency_jitter, 1 + self.latency_jitter)
        actual_latency = latency_ms * jitter / 1000.0  # Convert to seconds
        time.sleep(actual_latency)

    def complete(self, model: str = None, messages: list = None, **kwargs) -> MockAgentResponse:
        """Simulate complete() call."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)

        return MockAgentResponse(
            text=recorded.response_text,
            model=self.model,
            tokens_input=recorded.tokens_input,
            tokens_output=recorded.tokens_output,
            latency_ms=recorded.latency_ms,
        )

    def ask(self, instructions: str, **kwargs) -> MockAgentResponse:
        """Simulate ask() call."""
        return self.complete(**kwargs)

    def generate_dry_run_plan(self, context: str) -> MockAgentResponse:
        """Simulate plan generation."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)
        return MockAgentResponse(
            text=f"Mock plan for: {context[:50]}...",
            model=self.model,
            tokens_input=recorded.tokens_input,
            tokens_output=recorded.tokens_output,
        )

    def validate_dry_run_plan(self, intent: str, plan: str) -> Any:
        """Simulate plan validation."""
        self._apply_latency(300)  # Validation is fast
        return type("ValidationVerdict", (), {
            "approved": True,
            "corrections": "",
        })()

    def review_after_tests(self, **kwargs) -> str:
        """Simulate test review."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)
        return f"Mock review: tests {'passed' if random.random() > 0.3 else 'failed'}"

    def implement_with_tools(self, instructions: str, **kwargs) -> Any:
        """Simulate implementation."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)

        return type("OpusResult", (), {
            "text": f"Mock implementation: {instructions[:50]}...",
            "commands": ["git add -A", "git commit -m 'Mock commit'"],
            "steps": 3,
            "stopped": False,
            "last_commit_sha": "abc123def456",
        })()

    def bug_from_failure(self, instructions: str, test_output: str) -> str:
        """Simulate bug analysis."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)
        return f"Mock bug analysis: {instructions[:50]}... Fix: {test_output[:30]}..."

    def summarize_opus_output(self, text: str) -> str:
        """Simulate summarization."""
        self._apply_latency(200)
        return f"Mock summary of: {text[:50]}..."

    def generate_instructions(self, context: str, plan: str) -> str:
        """Simulate instruction generation."""
        self._apply_latency(300)
        return f"Mock instructions from plan: {plan[:50]}..."

    def revise_dry_run_plan(self, context: str, plan: str, corrections: str) -> str:
        """Simulate plan revision."""
        recorded = self._get_next_response()
        self._apply_latency(recorded.latency_ms)
        return f"Mock revised plan: {plan[:50]}..."


def load_recordings(session_id: str, recordings_dir: str | Path = ".recordings") -> list[RecordedResponse]:
    """Load recorded responses from disk."""
    recordings_dir = Path(recordings_dir)
    filepath = recordings_dir / f"{session_id}_responses.json"

    if not filepath.exists():
        logger.warning(f"No recordings found for {session_id}")
        return []

    with open(filepath) as f:
        data = json.load(f)

    responses = [
        RecordedResponse(
            timestamp=r["timestamp"],
            model=r["model"],
            prompt_hash=r["prompt_hash"],
            response_text=r["response_text"],
            tokens_input=r["tokens_input"],
            tokens_output=r["tokens_output"],
            latency_ms=r["latency_ms"],
        )
        for r in data
    ]

    logger.info(f"Loaded {len(responses)} recorded responses from {filepath}")
    return responses


def create_mock_agents(
    session_id: str = "demo",
    enable_latency: bool = True,
    recordings_dir: str | Path = ".recordings",
) -> tuple[MockAgent, MockAgent, MockAgent, Any]:
    """Create mock agents for testing.

    Args:
        session_id: which session's recordings to use
        enable_latency: whether to simulate realistic latency
        recordings_dir: where to load/save recordings

    Returns:
        (haiku, sonnet, opus, fake_usage_tracker)
    """
    # Load recordings for this session (or use empty list if not found)
    responses = load_recordings(session_id, recordings_dir)

    # Split responses by model for realistic behavior
    haiku_responses = [r for r in responses if "haiku" in r.model.lower()] or responses[::3]
    sonnet_responses = [r for r in responses if "sonnet" in r.model.lower()] or responses[1::3]
    opus_responses = [r for r in responses if "opus" in r.model.lower()] or responses[2::3]

    haiku = MockAgent("haiku", "claude-3-5-haiku-20241022", haiku_responses, enable_latency)
    sonnet = MockAgent("sonnet", "claude-3-5-sonnet-20241022", sonnet_responses, enable_latency)
    opus = MockAgent("opus", "claude-opus-4-1-20250805", opus_responses, enable_latency)

    # Fake usage tracker
    fake_tracker = type("UsageTracker", (), {
        "record": lambda *args, **kwargs: None,
        "summary": lambda: {"total_tokens": 0, "total_cost": 0},
    })()

    return haiku, sonnet, opus, fake_tracker
