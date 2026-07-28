#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Ollama /api/chat streaming adapter.

Ollama streams newline-delimited JSON (not SSE). Each line is a chunk
of the form:

    {"model":"llama3.1", "message":{"role":"assistant", "content":"Hi"}, "done":false}
    {"model":"llama3.1", "message":{"role":"assistant", "content":""},
     "done":true, "done_reason":"stop", "prompt_eval_count":42, "eval_count":13}

Tool calls come in as complete `message.tool_calls` arrays on the final
chunk (Ollama doesn't stream tool arguments token-by-token like OpenAI),
so we accumulate text deltas as they arrive and emit ToolUseStart/
Delta/End for each tool_call at the end.
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


logger = logging.getLogger('iris.chatbot.ollama')

_DEFAULT_OLLAMA_BASE = 'http://localhost:11434'
_STREAM_TIMEOUT = (10, 600)


class OllamaProvider(LLMProvider):
    name = 'ollama'

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        # Ollama typically runs unauthenticated on localhost, so
        # api_key is optional; when set, sent as a Bearer header for
        # proxy setups that add auth in front.
        self._api_key = api_key or ''
        self._base_url = (base_url or _DEFAULT_OLLAMA_BASE).rstrip('/')

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
        headers: dict[str, str] = {'content-type': 'application/json'}
        if self._api_key:
            headers['Authorization'] = f'Bearer {self._api_key}'
        url = f'{self._base_url}/api/chat'

        try:
            resp = requests.post(
                url, json=body, headers=headers,
                stream=True, timeout=_STREAM_TIMEOUT,
            )
        except requests.RequestException as exc:
            yield Error(message=f'ollama: connection error: {exc}')
            return

        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except Exception:
                err_body = {'error': resp.text[:500]}
            msg = err_body.get('error') or resp.text[:500]
            yield Error(message=f'ollama: {msg}')
            return

        stop_reason: str = 'end_turn'
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        tool_calls_pending: list[dict[str, Any]] = []

        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug('ollama: malformed chunk: %r', raw[:200])
                continue

            message = chunk.get('message') or {}
            text = message.get('content') or ''
            if text:
                yield TextDelta(text=text)

            for tc in message.get('tool_calls') or []:
                tool_calls_pending.append(tc)

            if chunk.get('done'):
                done_reason = chunk.get('done_reason') or 'stop'
                stop_reason = 'tool_use' if tool_calls_pending else 'end_turn'
                if done_reason == 'length':
                    stop_reason = 'max_tokens'
                prompt_tokens = chunk.get('prompt_eval_count')
                completion_tokens = chunk.get('eval_count')
                break

        # Emit accumulated tool_calls after the stream. Ollama doesn't
        # give us an id per tool_call, so we synthesise one — a stable
        # `ollama-<index>` id so the tool_result can reference it.
        for idx, tc in enumerate(tool_calls_pending):
            fn = tc.get('function') or {}
            tool_use_id = tc.get('id') or f'ollama-{idx}'
            name = fn.get('name') or ''
            args = fn.get('arguments') or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            yield ToolUseStart(tool_use_id=tool_use_id, name=name)
            yield ToolUseDelta(
                tool_use_id=tool_use_id,
                partial_json=json.dumps(args),
            )
            yield ToolUseEnd(
                tool_use_id=tool_use_id, name=name, arguments=args)

        yield MessageEnd(
            stop_reason=stop_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    @staticmethod
    def _build_body(
        model: str,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> dict[str, Any]:
        wire_messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': system}]
        for m in messages:
            wire_messages.extend(_ours_to_ollama_messages(m))
        body: dict[str, Any] = {
            'model': model,
            'messages': wire_messages,
            'stream': True,
            'options': {'num_predict': max_tokens},
        }
        if tools:
            body['tools'] = [_serialize_tool(t) for t in tools]
        return body


def _serialize_tool(t: ToolSpec) -> dict[str, Any]:
    """Ollama accepts OpenAI-shaped `{type: 'function', function: {...}}`
    for tool specs."""
    return {
        'type': 'function',
        'function': {
            'name': t.name,
            'description': t.description,
            'parameters': t.input_schema,
        },
    }


def _ours_to_ollama_messages(m: ChatMessage) -> list[dict[str, Any]]:
    """Anthropic-shaped content blocks → Ollama wire messages.

    Ollama uses OpenAI-style `role='tool'` messages with `tool_call_id`,
    so this mapping is nearly identical to the OpenAI adapter's — but
    kept separate because the two APIs' wire shapes have drifted before
    and are likely to again.
    """
    role = m.role
    blocks = m.content or []
    if role == 'user':
        return [{'role': 'user', 'content': _blocks_to_text(blocks)}]
    if role == 'assistant':
        text = _blocks_to_text(blocks)
        tool_calls: list[dict[str, Any]] = []
        for b in blocks:
            if b.get('type') == 'tool_use':
                tool_calls.append({
                    'id': b.get('id') or '',
                    'type': 'function',
                    'function': {
                        'name': b.get('name') or '',
                        'arguments': b.get('input') or {},
                    },
                })
        msg: dict[str, Any] = {'role': 'assistant'}
        if text:
            msg['content'] = text
        if tool_calls:
            msg['tool_calls'] = tool_calls
        return [msg]
    if role == 'tool':
        out: list[dict[str, Any]] = []
        for b in blocks:
            if b.get('type') != 'tool_result':
                continue
            content = b.get('content')
            if isinstance(content, list):
                content = _blocks_to_text(content)
            elif not isinstance(content, str):
                content = json.dumps(content)
            out.append({
                'role': 'tool',
                'tool_call_id': b.get('tool_use_id') or '',
                'content': content,
            })
        return out
    return []


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for b in blocks:
        if b.get('type') == 'text':
            parts.append(b.get('text') or '')
    return ''.join(parts)
