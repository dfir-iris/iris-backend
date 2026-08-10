#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Provider protocol + LLMEvent hierarchy shared by every adapter.

Each adapter yields the same sequence of events regardless of the
wire protocol. That lets the case-chat loop consume a single event
stream without knowing whether the tokens came from Anthropic's SSE,
OpenAI's chunked JSON, or Ollama's newline-delimited JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol


@dataclass(frozen=True)
class TextDelta:
    """Partial text output from the assistant — one or more characters
    at a time. The caller concatenates deltas to build the final text."""
    text: str


@dataclass(frozen=True)
class ToolUseStart:
    """Marks the start of a tool_use content block. `tool_use_id` is
    the opaque LLM-assigned identifier the corresponding tool_result
    will reference."""
    tool_use_id: str
    name: str


@dataclass(frozen=True)
class ToolUseDelta:
    """Streamed partial JSON of a tool_use `input` object. Adapters
    that don't stream tool arguments (OpenAI does token-by-token, but
    Ollama sends the whole object at once) still emit a single
    ToolUseDelta with the complete JSON so the consumer's accumulator
    logic is uniform."""
    tool_use_id: str
    partial_json: str


@dataclass(frozen=True)
class ToolUseEnd:
    """End of a tool_use block. `arguments` is the fully-parsed dict."""
    tool_use_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class MessageEnd:
    """Terminator emitted after the model finishes a turn. `stop_reason`
    is Anthropic-shaped: 'end_turn' | 'tool_use' | 'max_tokens' | 'stop'.
    Adapters map their provider's native stop reason to this set.

    `cache_read_tokens` / `cache_creation_tokens` come from providers
    that report prompt-cache accounting: Anthropic emits both on
    `message_start.usage`, OpenAI emits cache_read only (cached_tokens
    on prompt_tokens_details), Ollama emits neither and leaves both None.
    """
    stop_reason: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


@dataclass(frozen=True)
class Error:
    """Terminal event when the provider returns an error mid-stream.
    The loop surfaces this to the client as an error socket event."""
    message: str
    provider_code: str | None = None


LLMEvent = TextDelta | ToolUseStart | ToolUseDelta | ToolUseEnd | MessageEnd | Error


@dataclass(frozen=True)
class ToolSpec:
    """The subset of an MCP ToolSpec each provider adapter needs. Kept
    separate from `app.blueprints.rest.v2.mcp.registry.ToolSpec` so the
    llm engine doesn't depend on the API layer (import-linter contract).
    """
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    """One turn in the message history sent to the LLM.

    `content` is a list of Anthropic-shaped content blocks. Adapters
    for providers that use a different wire model (OpenAI's flat
    text-only + tool_calls array) convert on serialisation.
    """
    role: str  # 'user' | 'assistant' | 'tool'
    content: list[dict[str, Any]] = field(default_factory=list)


class LLMProvider(Protocol):
    """Adapter contract.

    `stream_completion` blocks until the stream is exhausted, yielding
    events as they arrive. Under gevent, each yield is a cooperative
    yield-point — the worker is free to service other events between
    tokens.
    """
    name: str

    def stream_completion(
        self,
        *,
        model: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_tokens: int = 4096,
    ) -> Iterator[LLMEvent]:
        ...
