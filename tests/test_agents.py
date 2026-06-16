"""Phase 2 — agent layer: capability gating, usage, payload guard, dry-run."""
from __future__ import annotations

import json

import pytest

from app.agents import build_agents, run_dry_run
from app.agents.client import AnthropicClient
from app.agents.payload import (PAYLOAD_TOKEN_LIMIT, PrioritizedSummary,
                                enforce_payload_limit)
from app.agents.tokens import estimate_tokens
from app.errors import PayloadTooLargeError
from tests.fakes import FakeMessage, FakeSDK, _Usage


# --------------------------- capability gating --------------------------- #
def test_effort_and_thinking_for_opus_and_sonnet(config):
    sdk = FakeSDK()
    client = AnthropicClient(config, sdk_client=sdk)
    for model in ("claude-opus-4-8", "claude-sonnet-4-6"):
        client.complete(model=model, messages=[{"role": "user", "content": "hi"}],
                        effort="high")
    for call in sdk.calls:
        assert call["thinking"] == {"type": "adaptive"}
        assert call["output_config"]["effort"] == "high"


def test_haiku_omits_effort_and_thinking(config):
    sdk = FakeSDK()
    client = AnthropicClient(config, sdk_client=sdk)
    # Pass effort explicitly — it must still be dropped for Haiku (would 400).
    client.complete(model="claude-haiku-4-5",
                    messages=[{"role": "user", "content": "hi"}], effort="medium")
    call = sdk.calls[0]
    assert "thinking" not in call
    assert "output_config" not in call


def test_no_sampling_params_ever(config):
    sdk = FakeSDK()
    client = AnthropicClient(config, sdk_client=sdk)
    client.complete(model="claude-opus-4-8",
                    messages=[{"role": "user", "content": "hi"}], effort="high")
    call = sdk.calls[0]
    for banned in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert banned not in call
        assert banned not in call.get("thinking", {})


def test_output_config_preserves_format_without_effort_on_haiku(config):
    sdk = FakeSDK()
    client = AnthropicClient(config, sdk_client=sdk)
    fmt = {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    client.complete(model="claude-haiku-4-5",
                    messages=[{"role": "user", "content": "hi"}],
                    effort="medium", output_config=fmt)
    call = sdk.calls[0]
    assert call["output_config"] == fmt          # format kept
    assert "effort" not in call["output_config"]  # effort dropped for Haiku


# --------------------------- usage + text extraction --------------------------- #
def test_usage_recorded(config):
    sdk = FakeSDK(lambda p: FakeMessage("hello", usage=_Usage(i=100, o=40)))
    client = AnthropicClient(config, sdk_client=sdk)
    resp = client.complete(model="claude-opus-4-8",
                           messages=[{"role": "user", "content": "hi"}],
                           agent_label="opus")
    assert resp.text == "hello"
    assert resp.usage.output_tokens == 40
    assert client.usage.total.input_tokens == 100
    assert client.usage.by_agent["opus"].output_tokens == 40
    assert client.usage.by_model["claude-opus-4-8"].input_tokens == 100


def test_text_extraction_ignores_thinking_blocks(config):
    sdk = FakeSDK(lambda p: FakeMessage("answer", include_thinking=True))
    client = AnthropicClient(config, sdk_client=sdk)
    resp = client.complete(model="claude-opus-4-8",
                           messages=[{"role": "user", "content": "hi"}])
    assert resp.text == "answer"


def test_streaming_path_uses_stream_and_records_usage(config):
    sdk = FakeSDK(lambda p: FakeMessage("streamed", usage=_Usage(o=5)))
    client = AnthropicClient(config, sdk_client=sdk)
    resp = client.complete(model="claude-opus-4-8",
                           messages=[{"role": "user", "content": "hi"}],
                           stream=True)
    assert resp.text == "streamed"
    assert client.usage.total.output_tokens == 5


# --------------------------- payload guard --------------------------- #
def test_payload_under_limit_passthrough():
    assert enforce_payload_limit("short text") == "short text"


def test_payload_reducer_brings_into_budget():
    big = "x" * (PAYLOAD_TOKEN_LIMIT * 4 + 100)   # ~over limit by estimate
    out = enforce_payload_limit(big, reducer=lambda t, lim: "tiny")
    assert out == "tiny"


def test_payload_raises_without_reducer():
    big = "x" * (PAYLOAD_TOKEN_LIMIT * 4 + 100)
    with pytest.raises(PayloadTooLargeError):
        enforce_payload_limit(big)


def test_prioritized_summary_drops_lowest_priority_when_over_budget():
    summary = PrioritizedSummary(
        error="boom",
        changed_files="a.py",
        reasoning="y" * (PAYLOAD_TOKEN_LIMIT * 4),  # huge reasoning
    )
    rendered = summary.render()
    assert "ERROR" in rendered and "boom" in rendered
    assert "CHANGED FILES" in rendered
    assert "REASONING" not in rendered   # dropped to stay in budget


# --------------------------- dry-run handshake --------------------------- #
def _verdict(approved: bool, corrections: str = "") -> str:
    return json.dumps({"approved": approved, "corrections": corrections})


def _make_responder(verdict_sequence):
    """Sonnet returns plans/instructions; Haiku returns scripted verdicts."""
    state = {"i": 0}

    def responder(params):
        model = params["model"]
        content = params["messages"][0]["content"]
        if "haiku" in model:
            v = verdict_sequence[min(state["i"], len(verdict_sequence) - 1)]
            state["i"] += 1
            return FakeMessage(v)
        # sonnet
        if "INSTRUCTIONS for Opus" in content:
            return FakeMessage("INSTRUCTIONS: edit navbar only")
        if content.startswith("Revise"):
            return FakeMessage("PLAN v2: edit navbar only")
        return FakeMessage("PLAN: edit navbar")
    return responder


def test_dry_run_approved_first_round(config):
    sdk = FakeSDK(_make_responder([_verdict(True)]))
    haiku, sonnet, opus, tracker = build_agents(config, sdk_client=sdk)
    result = run_dry_run(sonnet, haiku, context="ctx",
                         original_intent="add navbar toggle")
    assert result.approved is True
    assert result.rounds == 1
    assert result.instructions == "INSTRUCTIONS: edit navbar only"


def test_dry_run_correction_loop_then_approve(config):
    sdk = FakeSDK(_make_responder([_verdict(False, "don't touch auth"),
                                   _verdict(True)]))
    haiku, sonnet, opus, tracker = build_agents(config, sdk_client=sdk)
    result = run_dry_run(sonnet, haiku, context="ctx",
                         original_intent="add navbar toggle")
    assert result.approved is True
    assert result.rounds == 2
    assert len(result.verdicts) == 2
    assert result.plan == "PLAN v2: edit navbar only"  # revised plan used
    assert result.instructions == "INSTRUCTIONS: edit navbar only"


def test_dry_run_exhausts_rounds_without_approval(config):
    sdk = FakeSDK(_make_responder([_verdict(False, "still wrong")]))
    haiku, sonnet, opus, tracker = build_agents(config, sdk_client=sdk)
    result = run_dry_run(sonnet, haiku, context="ctx",
                         original_intent="add navbar toggle", max_rounds=3)
    assert result.approved is False
    assert result.rounds == 3
    assert result.instructions == ""   # no instructions from an unapproved plan


def test_haiku_validation_disables_thinking_and_requests_json(config):
    sdk = FakeSDK(_make_responder([_verdict(True)]))
    haiku, sonnet, opus, tracker = build_agents(config, sdk_client=sdk)
    haiku.validate_dry_run_plan("intent", "plan")
    call = sdk.calls[0]
    assert "thinking" not in call                      # Haiku: no adaptive thinking
    assert call["output_config"]["format"]["type"] == "json_schema"
