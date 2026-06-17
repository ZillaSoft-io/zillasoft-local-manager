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


def build_agents(config, sdk_client=None, mock_mode=False):
    """Construct the three role agents sharing one client + usage tracker.

    Args:
        config: configuration handler
        sdk_client: injected client for testing (or None for real)
        mock_mode: if True, use mock agents that replay recorded responses
                  without hitting the API (for testing at $0 cost)

    Returns:
        (haiku, sonnet, opus, tracker) tuple
    """
    import logging
    logger = logging.getLogger(__name__)

    # Option 2: Mock + Replay mode for testing
    if mock_mode:
        from ..mock_agents import create_mock_agents
        session_id = config.get_raw("MOCK_SESSION_ID", "demo")
        enable_latency = config.get_raw("MOCK_ENABLE_LATENCY", "true").lower() == "true"
        logger.info(f"Using mock agents (session: {session_id}, latency: {enable_latency})")
        return create_mock_agents(session_id, enable_latency=enable_latency)

    # Real agents
    tracker = UsageTracker()
    client = AnthropicClient(config, sdk_client=sdk_client, usage_tracker=tracker)
    haiku = HaikuAgent(client, config)
    sonnet = SonnetAgent(client, config)
    opus = OpusAgent(client, config)
    return haiku, sonnet, opus, tracker
