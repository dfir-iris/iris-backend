#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""OpenAI /v1/chat/completions streaming adapter.

Two shape differences from Anthropic that this adapter smooths over:

  1. **Message shape**: OpenAI uses `role='tool'` messages with a flat
     `content: str` + a top-level `tool_call_id`, and `role='assistant'`
     messages carry `tool_calls: [{id, function:{name, arguments}}]`
     rather than inlined `tool_use` blocks. `_serialize_message` maps
     from our Anthropic-shaped content-block persistence back to this
     wire shape.

  2. **Streaming**: OpenAI streams `chunk` objects with
     `choices[0].delta` containing partial text OR partial
     `tool_calls[i].function.arguments` JSON. Tool calls are accumulated
     by `index`. The final chunk has `finish_reason` set.

The event stream we yield is identical to Anthropic's so the loop is
unchanged.
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


logger = logging.getLogger('iris.chatbot.openai')

_OPENAI_BASE = 'https://api.openai.com'
_STREAM_TIMEOUT = (10, 300)

# OpenAI finish_reason → Anthropic-shaped stop_reason.
_FINISH_REASON_MAP = {
    'stop': 'end_turn',
    'length': 'max_tokens',
    'tool_calls': 'tool_use',
    'function_call': 'tool_use',  # legacy
    'content_filter': 'end_turn',
}


class OpenAIProvider(LLMProvider):
    name = 'openai'

    def __init__(self, *, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError('OpenAIProvider requires an api_key')
        self._api_key = api_key
        self._base_url = base_url.rstrip('/') if base_url else _OPENAI_BASE

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
            'Authorization': f'Bearer {self._api_key}',
            'content-type': 'application/json',
        }
        url = f'{self._base_url}/v1/chat/completions'

        try:
            resp = requests.post(
                url, json=body, headers=headers,
                stream=True, timeout=_STREAM_TIMEOUT,
            )
        except requests.RequestException as exc:
            yield Error(message=f'openai: connection error: {exc}')
            return

        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except Exception:
                err_body = {'error': {'message': resp.text[:500]}}
            msg = err_body.get('error', {}).get('message') or resp.text[:500]
            code = err_body.get('error', {}).get('code')
            yield Error(message=f'openai: {msg}', provider_code=code)
            return

        # Accumulator for tool-call deltas keyed by `index` (OpenAI
        # numbers parallel tool_calls the same way Anthropic numbers
        # content blocks).
        tool_calls: dict[int, dict[str, Any]] = {}
        emitted_start: set[int] = set()
        finish_reason: str = 'end_turn'
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        cache_read_tokens: int | None = None

        for chunk in _iter_openai_chunks(resp):
            usage = chunk.get('usage')
            if usage:
                prompt_tokens = usage.get('prompt_tokens')
                completion_tokens = usage.get('completion_tokens')
                # Cache hits (`prompt_tokens_details.cached_tokens`) —
                # emitted on models with automatic prompt caching. OpenAI
                # has no cache-write concept, so `cache_creation_tokens`
                # stays None here.
                details = usage.get('prompt_tokens_details') or {}
                if details.get('cached_tokens') is not None:
                    cache_read_tokens = details['cached_tokens']
                # `usage` typically appears in the final chunk with
                # `choices: []`; safe to continue processing this frame.

            choices = chunk.get('choices') or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get('delta') or {}

            text = delta.get('content')
            if isinstance(text, str) and text:
                yield TextDelta(text=text)

            for tc_delta in delta.get('tool_calls') or []:
                idx = tc_delta.get('index', 0)
                entry = tool_calls.setdefault(idx, {
                    'id': '', 'name': '', 'partial_json': '',
                })
                if tc_delta.get('id'):
                    entry['id'] = tc_delta['id']
                fn = tc_delta.get('function') or {}
                if fn.get('name'):
                    entry['name'] = fn['name']
                if idx not in emitted_start and entry['id'] and entry['name']:
                    emitted_start.add(idx)
                    yield ToolUseStart(
                        tool_use_id=entry['id'], name=entry['name'])
                arg_chunk = fn.get('arguments')
                if isinstance(arg_chunk, str) and arg_chunk:
                    entry['partial_json'] += arg_chunk
                    if entry['id']:
                        yield ToolUseDelta(
                            tool_use_id=entry['id'],
                            partial_json=arg_chunk,
                        )

            raw_finish = choice.get('finish_reason')
            if raw_finish:
                finish_reason = _FINISH_REASON_MAP.get(raw_finish, 'end_turn')

        # After the stream ends, finalise every accumulated tool_call.
        for entry in tool_calls.values():
            if not entry['id']:
                continue
            try:
                args = json.loads(entry['partial_json'] or '{}')
            except json.JSONDecodeError:
                args = {}
            yield ToolUseEnd(
                tool_use_id=entry['id'],
                name=entry['name'],
                arguments=args,
            )

        yield MessageEnd(
            stop_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
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
        wire_messages: list[dict[str, Any]] = [
            {'role': 'system', 'content': system}]
        for m in messages:
            wire_messages.extend(_ours_to_openai_messages(m))
        body: dict[str, Any] = {
            'model': model,
            'messages': wire_messages,
            'max_tokens': max_tokens,
            'stream': True,
            # `stream_options.include_usage=true` makes OpenAI emit a
            # final chunk with prompt/completion token counts.
            'stream_options': {'include_usage': True},
        }
        if tools:
            body['tools'] = [_serialize_tool(t) for t in tools]
            body['tool_choice'] = 'auto'
        return body


def _serialize_tool(t: ToolSpec) -> dict[str, Any]:
    return {
        'type': 'function',
        'function': {
            'name': t.name,
            'description': t.description,
            'parameters': t.input_schema,
        },
    }


def _ours_to_openai_messages(m: ChatMessage) -> list[dict[str, Any]]:
    """Anthropic-shaped content blocks → OpenAI wire messages.

    One `ChatMessage` can produce N OpenAI messages: `role='tool'`
    messages must be split one-per-tool_result on OpenAI's wire (they
    each carry a single `tool_call_id`), and assistants that mix text
    with tool_use produce one assistant message with both `content`
    and `tool_calls` fields.
    """
    role = m.role
    blocks = m.content or []
    if role == 'user':
        # User messages are always plain text — flatten any content
        # blocks into a single string.
        text = _blocks_to_text(blocks)
        return [{'role': 'user', 'content': text}]
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
                        'arguments': json.dumps(b.get('input') or {}),
                    },
                })
        msg: dict[str, Any] = {'role': 'assistant'}
        if text:
            msg['content'] = text
        if tool_calls:
            msg['tool_calls'] = tool_calls
        return [msg]
    if role == 'tool':
        # One OpenAI 'tool' message per tool_result block.
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


def _iter_openai_chunks(resp: requests.Response) -> Iterator[dict[str, Any]]:
    """Iterate JSON-decoded chunk objects from an OpenAI SSE stream.

    Each frame is `data: {json}` with `[DONE]` as the terminator. We
    silently drop malformed frames — OpenAI has never actually emitted
    one in practice but the defensive parse keeps a single bad byte
    from tearing the whole stream down.
    """
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.rstrip('\r')
        if not line.startswith('data:'):
            continue
        data = line.split(':', 1)[1].lstrip()
        if data == '[DONE]':
            break
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            logger.debug('openai: malformed chunk: %r', data)
