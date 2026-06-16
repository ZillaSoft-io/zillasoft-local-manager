"""A fake Anthropic SDK client for offline agent tests.

Mimics the small surface AnthropicClient touches: messages.create,
messages.stream (context manager + get_final_message), messages.count_tokens.
Records every request's params so tests can assert capability gating.
"""
from __future__ import annotations

from typing import Callable


class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _ThinkingBlock:
    def __init__(self, text: str):
        self.type = "thinking"
        self.thinking = text


class _Usage:
    def __init__(self, i=10, o=20, cc=0, cr=0):
        self.input_tokens = i
        self.output_tokens = o
        self.cache_creation_input_tokens = cc
        self.cache_read_input_tokens = cr


class _ToolUseBlock:
    def __init__(self, name: str, tool_input: dict, block_id: str = "tu1"):
        self.type = "tool_use"
        self.name = name
        self.input = tool_input
        self.id = block_id


class FakeMessage:
    def __init__(self, text: str, usage: _Usage | None = None,
                 stop_reason: str = "end_turn", include_thinking: bool = False):
        self.content = ([_ThinkingBlock("reasoning...")] if include_thinking else []) \
            + [_Block(text)]
        self.usage = usage or _Usage()
        self.stop_reason = stop_reason


def tool_use_message(name: str, tool_input: dict, text: str = "",
                     block_id: str = "tu1") -> FakeMessage:
    """A response that requests a tool call (stop_reason='tool_use')."""
    msg = FakeMessage(text, stop_reason="tool_use")
    blocks = [_ToolUseBlock(name, tool_input, block_id)]
    if text:
        blocks.insert(0, _Block(text))
    msg.content = blocks
    return msg


class _FakeStream:
    def __init__(self, message: FakeMessage):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class _Count:
    def __init__(self, n: int):
        self.input_tokens = n


class _FakeMessages:
    def __init__(self, parent: "FakeSDK"):
        self._parent = parent

    def create(self, **params):
        self._parent.calls.append(params)
        return self._parent.responder(params)

    def stream(self, **params):
        self._parent.calls.append(params)
        return _FakeStream(self._parent.responder(params))

    def count_tokens(self, **params):
        text = params["messages"][0]["content"]
        return _Count(len(text) // 4)


class FakeSDK:
    """Injectable stand-in for `anthropic.Anthropic`."""

    def __init__(self, responder: Callable[[dict], FakeMessage] | None = None):
        self.calls: list[dict] = []
        self.responder = responder or (lambda params: FakeMessage("ok"))
        self.messages = _FakeMessages(self)
