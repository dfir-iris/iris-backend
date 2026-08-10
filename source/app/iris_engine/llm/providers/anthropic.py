#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Anthropic /v1/messages streaming adapter.

Uses the sync `requests` client with `stream=True` + `iter_lines()`. The
Anthropic SSE stream looks like:

    event: message_start
    data: {"type":"message_start", "message":{...}}

    event: content_block_start
    data: {"type":"content_block_start", "index":0, "content_block":{"type":"text"}}

    event: content_block_delta
    data: {"type":"content_block_delta", "index":0, "delta":{"type":"text_delta", "text":"Hi"}}

    ...

    event: message_delta
    data: {"type":"message_delta", "delta":{"stop_reason":"end_turn"}, "usage":{"output_tokens":42}}

    event: message_stop
    data: {"type":"message_stop"}

We map each event type into the shared `LLMEvent` hierarchy so the
case-chat loop stays provider-agnostic.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import requests

from app.iris_engine.llm.providers.base import (
    ChatMessage,
    Error,
    LLMEvent,
    LLMProvider,
    MessageEnd,
    TextDelta,
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
)


logger = logging.getLogger('iris.chatbot.anthropic')

_ANTHROPIC_BASE = 'https://api.anthropic.com'
_ANTHROPIC_VERSION = '2023-06-01'
_STREAM_TIMEOUT = (10, 300)  # (connect, read) seconds


class AnthropicProvider(LLMProvider):
    name = 'anthropic'

    def __init__(self, *, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError('AnthropicProvider requires an api_key')
        self._api_key = api_key
        self._base_url = base_url.rstrip('/') if base_url else _ANTHROPIC_BASE

    # -----------------------------------------------------------------

    def stream_completion(
        self,
        *,
        model: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_tokens: int = 4096,
    ) -> Iterator[LLMEvent]:
        body = self._build_body(model, system, messages, tools, max_tokens)
        headers = {
            'x-api-key': self._api_key,
            'anthropic-version': _ANTHROPIC_VERSION,
            'content-type': 'application/json',
        }
        url = f'{self._base_url}/v1/messages'

        try:
            resp = requests.post(
                url, json=body, headers=headers,
                stream=True, timeout=_STREAM_TIMEOUT,
            )
        except requests.RequestException as exc:
            yield Error(message=f'anthropic: connection error: {exc}')
            return

        if resp.status_code != 200:
            # Anthropic returns a JSON error body on non-2xx.
            try:
                err_body = resp.json()
            except Exception:
                err_body = {'error': {'message': resp.text[:500]}}
            msg = err_body.get('error', {}).get('message') or resp.text[:500]
            code = err_body.get('error', {}).get('type')
            yield Error(message=f'anthropic: {msg}', provider_code=code)
            return

        # Track partial tool_use blocks across content_block_start /
        # _delta / _stop. Keyed by `index` (Anthropic's block index),
        # value is a dict with `tool_use_id`, `name`, `partial_json`.
        pending_tool_use: dict[int, dict[str, Any]] = {}
        stop_reason: str = 'end_turn'
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        cache_read_tokens: int | None = None
        cache_creation_tokens: int | None = None

        for event_type, payload in _iter_sse(resp):
            if event_type in ('ping', ''):
                continue
            if event_type == 'message_start':
                usage = payload.get('message', {}).get('usage') or {}
                prompt_tokens = usage.get('input_tokens')
                # Prompt-cache counters — present when the request used
                # `cache_control` blocks and the model supports caching.
                # Absent on non-cache models; leave as None so the audit
                # row doesn't misreport "0 cache" as if caching was on.
                if usage.get('cache_read_input_tokens') is not None:
                    cache_read_tokens = usage['cache_read_input_tokens']
                if usage.get('cache_creation_input_tokens') is not None:
                    cache_creation_tokens = usage['cache_creation_input_tokens']
                continue
            if event_type == 'content_block_start':
                block = payload.get('content_block') or {}
                idx = payload.get('index', 0)
                if block.get('type') == 'tool_use':
                    tool_use_id = block.get('id') or ''
                    name = block.get('name') or ''
                    pending_tool_use[idx] = {
                        'tool_use_id': tool_use_id,
                        'name': name,
                        'partial_json': '',
                    }
                    yield ToolUseStart(tool_use_id=tool_use_id, name=name)
                continue
            if event_type == 'content_block_delta':
                delta = payload.get('delta') or {}
                idx = payload.get('index', 0)
                dtype = delta.get('type')
                if dtype == 'text_delta':
                    text = delta.get('text') or ''
                    if text:
                        yield TextDelta(text=text)
                elif dtype == 'input_json_delta':
                    entry = pending_tool_use.get(idx)
                    if entry is None:
                        continue
                    chunk = delta.get('partial_json') or ''
                    entry['partial_json'] += chunk
                    yield ToolUseDelta(
                        tool_use_id=entry['tool_use_id'],
                        partial_json=chunk,
                    )
                continue
            if event_type == 'content_block_stop':
                idx = payload.get('index', 0)
                entry = pending_tool_use.pop(idx, None)
                if entry is None:
                    continue
                try:
                    args = json.loads(entry['partial_json'] or '{}')
                except json.JSONDecodeError:
                    args = {}
                yield ToolUseEnd(
                    tool_use_id=entry['tool_use_id'],
                    name=entry['name'],
                    arguments=args,
                )
                continue
            if event_type == 'message_delta':
                delta = payload.get('delta') or {}
                if delta.get('stop_reason'):
                    stop_reason = delta['stop_reason']
                usage = payload.get('usage') or {}
                if usage.get('output_tokens') is not None:
                    completion_tokens = usage['output_tokens']
                continue
            if event_type == 'message_stop':
                break
            # Unknown event type — Anthropic occasionally adds new ones
            # (e.g. `ping`, `error`). Handle `error` specially, ignore
            # anything else forward-compatibly.
            if event_type == 'error':
                err = payload.get('error', {})
                yield Error(
                    message=err.get('message', 'anthropic stream error'),
                    provider_code=err.get('type'),
                )
                return

        yield MessageEnd(
            stop_reason=stop_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    # -----------------------------------------------------------------

    @staticmethod
    def _build_body(
        model: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> dict[str, Any]:
        return {
            'model': model,
            'system': system,
            'messages': [_serialize_message(m) for m in messages],
            'tools': [_serialize_tool(t) for t in tools],
            'max_tokens': max_tokens,
            'stream': True,
        }


def _serialize_message(m: ChatMessage) -> dict[str, Any]:
    """Convert our internal message shape to Anthropic's wire format.

    Our loop persists tool results as `role='tool'` (matches OpenAI's
    convention and keeps the classifier's read/write bookkeeping
    symmetric). Anthropic's API only accepts `user` / `assistant`, and
    expects tool-result blocks inside a `user` turn:

        {
          role: "user",
          content: [{type: "tool_result", tool_use_id: "...", content: [...]}]
        }

    Our stored content already carries the `tool_result` block shape,
    so the only fix is flipping the role label — no restructuring
    needed on the way out.
    """
    role = 'user' if m.role == 'tool' else m.role
    return {'role': role, 'content': m.content}


def _serialize_tool(t: ToolSpec) -> dict[str, Any]:
    return {
        'name': t.name,
        'description': t.description,
        'input_schema': t.input_schema,
    }


def _iter_sse(resp: requests.Response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Iterate `(event_type, payload_dict)` tuples from an SSE stream.

    Skips comment lines and empty frames. Payload is JSON-decoded; a
    malformed data line is silently dropped rather than aborting the
    whole stream (we've seen Anthropic occasionally emit heartbeat
    comments interleaved with events).
    """
    event_type = ''
    data_buf: list[str] = []
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.rstrip('\r')
        if line == '':
            # End of an event frame.
            if data_buf:
                joined = '\n'.join(data_buf)
                try:
                    payload = json.loads(joined)
                except json.JSONDecodeError:
                    logger.debug('anthropic: malformed data frame: %r', joined)
                    payload = {}
                yield event_type, payload
            event_type = ''
            data_buf = []
            continue
        if line.startswith(':'):
            # SSE comment (keepalive) — ignore.
            continue
        if line.startswith('event:'):
            event_type = line.split(':', 1)[1].strip()
            continue
        if line.startswith('data:'):
            data_buf.append(line.split(':', 1)[1].lstrip())
            continue
