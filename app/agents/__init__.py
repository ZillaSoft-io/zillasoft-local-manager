"""Agent layer — Anthropic SDK integration and inter-agent orchestration.

Exposes the three role agents (Haiku, Sonnet, Opus), the shared Anthropic
client wrapper, usage tracking, and the dry-run validation handshake.
"""
from .client import AnthropicClient, AgentResponse, Usage
from .usage import UsageTracker
from .haiku import HaikuAgent
from .sonnet import SonnetAgent
from .opus import OpusAgent
from .dry_run import DryRunResult, run_dry_run
from .payload import PAYLOAD_TOKEN_LIMIT, enforce_payload_limit

__all__ = [
    "AnthropicClient",
    "AgentResponse",
    "Usage",
    "UsageTracker",
    "HaikuAgent",
    "SonnetAgent",
    "OpusAgent",
    "DryRunResult",
    "run_dry_run",
    "PAYLOAD_TOKEN_LIMIT",
    "enforce_payload_limit",
    "build_agents",
]


def build_agents(config, sdk_client=None):
    """Construct the three role agents sharing one client + usage tracker.

    `sdk_client` lets callers inject a fake/mock Anthropic client for testing.
    """
    tracker = UsageTracker()
    client = AnthropicClient(config, sdk_client=sdk_client, usage_tracker=tracker)
    haiku = HaikuAgent(client, config)
    sonnet = SonnetAgent(client, config)
    opus = OpusAgent(client, config)
    return haiku, sonnet, opus, tracker
