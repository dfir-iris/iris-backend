#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Stateless tool-use loop.

Called from `send`, `approve_tool`, and `deny_tool`; each invocation
runs one loop iteration by:

  1. Loading conversation history from the DB.
  2. Checking daily token budgets.
  3. Building the tools list from MCP + stripping the injected
     `case_identifier` when the conversation is case-scoped.
  4. Applying redaction to tool_result blocks in history.
  5. Streaming from the provider, emitting socket events, and either:
     - auto-executing read tools inline via `dispatch_tool_call`, or
     - persisting `case_chat_pending_tool_call` rows for writes and
       stopping until the user resolves them.
  6. On `stop_reason=='tool_use'` with only auto-executed reads,
     re-entering the loop with the freshly appended tool_result.

Zero in-memory pause primitives. Tab-close is a no-op; the conversation
resumes tomorrow with the same pending rows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.case_chat import tool_classification
from app.blueprints.rest.v2.case_chat.tool_classification import (
    UnclassifiedToolError,
)
from app.blueprints.rest.v2.mcp.dispatch import (
    MCPError,
    build_tools_list,
    dispatch_tool_call,
)
from app.business import case_chat as case_chat_biz
from app.iris_engine.llm.client import (
    ChatbotConfig,
    ChatbotDisabledError,
    get_llm_provider,
    load_config,
)
from app.iris_engine.llm.providers.base import (
    ChatMessage,
    Error,
    LLMEvent,
    MessageEnd,
    TextDelta,
    ToolSpec,
    ToolUseEnd,
)
from app.iris_engine.llm.redaction import redact_content_blocks
from app.iris_engine.llm.system_prompt import system_prompt
from app.iris_engine.utils.tracker import track_activity
from app.models.case_chat import (
    ROLE_ASSISTANT,
    ROLE_TOOL,
    CaseChatConversation,
)


logger = logging.getLogger('iris.chatbot.loop')


# ---- Event emitter --------------------------------------------------

@dataclass(frozen=True)
class ChatEvent:
    """One outbound event from the loop to the client. Kept protocol-
    agnostic so the namespace layer can convert to SocketIO emits (or,
    if we ever add SSE, translate here)."""
    name: str
    payload: dict[str, Any]


Emitter = Callable[[ChatEvent], None]


# ---- Public entrypoint ---------------------------------------------

def run_one_iteration(
    *,
    conversation: CaseChatConversation,
    emit: Emitter,
) -> None:
    """Run one LLM turn. May persist several messages (assistant text,
    zero-or-more auto-executed tool_results). Stops the moment a write
    tool_use is emitted — the client resolves it and re-invokes this
    function via `approve_tool` / `deny_tool`.

    The loop is a straight-line generator consumer under gevent —
    `for event in provider.stream_completion(...)` yields cooperatively
    between tokens, so a long stream never blocks the worker.
    """
    try:
        cfg = load_config()
    except Exception as exc:
        logger.exception('failed to load chatbot config')
        emit(ChatEvent('error', {
            'message': f'Chatbot configuration error: {exc}',
        }))
        return

    if not cfg.enabled:
        emit(ChatEvent('error', {
            'message': 'Chatbot is disabled on this server.',
        }))
        return

    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]

    # Budget check — refuse before spending an LLM token.
    if cfg.daily_token_budget_per_user > 0:
        spent_user = case_chat_biz.sum_tokens_today(user_id=user.id)
        if spent_user >= cfg.daily_token_budget_per_user:
            emit(ChatEvent('error', {
                'message': (
                    f'Daily per-user chatbot budget exhausted '
                    f'({spent_user}/{cfg.daily_token_budget_per_user} tokens). '
                    'Try again tomorrow or ask an admin to raise the cap.'
                ),
            }))
            return
    if cfg.daily_token_budget_org > 0:
        spent_org = case_chat_biz.sum_tokens_today()
        if spent_org >= cfg.daily_token_budget_org:
            emit(ChatEvent('error', {
                'message': (
                    f'Daily org-wide chatbot budget exhausted '
                    f'({spent_org}/{cfg.daily_token_budget_org} tokens).'
                ),
            }))
            return

    try:
        provider = get_llm_provider(cfg)
    except ChatbotDisabledError as exc:
        emit(ChatEvent('error', {'message': str(exc)}))
        return

    # Iterate until we either hit stop_reason=='end_turn' (done) or a
    # write tool_use pauses us (return). Auto-executed reads loop back
    # here without a per-iteration user event.
    max_iterations = max(1, cfg.max_tool_calls_per_turn)
    for _turn in range(max_iterations):
        history = _load_history(conversation, cfg)
        tools = _prepare_tools(conversation)
        system = system_prompt(
            case_id=conversation.case_id,
            war_room_id=conversation.war_room_id,
        )

        request_bytes = _estimate_request_bytes(system, history, tools)
        # Redaction toggles are checked inside `_load_history`, so the
        # `redacted` bit here reflects what was actually scrubbed.
        redacted = _history_was_redacted(history, cfg)

        audit_row = case_chat_biz.record_egress(
            conversation=conversation,
            user=user,
            provider=provider.name,
            model=cfg.model,
            request_bytes=request_bytes,
            redacted=redacted,
        )

        turn_result = _consume_stream(
            provider=provider,
            model=cfg.model,
            system=system,
            history=history,
            tools=tools,
            conversation=conversation,
            audit_row=audit_row,
            emit=emit,
        )

        if turn_result.terminated:
            return
        if turn_result.paused_on_write:
            return
        # All-reads turn → loop back and let the model see the results.


# ---- Internals -----------------------------------------------------

def _tool_result_content(result: Any) -> Any:
    """Coerce a `dispatch_tool_call` return into a shape Anthropic
    accepts for `tool_result.content`.

    Anthropic requires either a string or a list of content blocks
    (`[{type: 'text', text: '...'}, ...]`). Our MCP dispatch returns
    Python dicts / lists / primitives, so we JSON-encode anything
    non-string. Kept as a helper so approve_tool.py (write dispatch)
    and this file (read dispatch) share one funnel — the shape has to
    be identical for the provider adapter to accept it.
    """
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except (TypeError, ValueError):
        return str(result)


@dataclass
class _TurnResult:
    terminated: bool = False       # end_turn / stop / error — done for now
    paused_on_write: bool = False  # write tool_use written to DB; wait for approve/deny


def _load_history(
    conversation: CaseChatConversation, cfg: ChatbotConfig,
) -> list[ChatMessage]:
    """Load messages + apply the max-turns trim + apply redaction to
    role='tool' blocks. Returns fully-materialised `ChatMessage`
    dataclasses ready to hand to a provider adapter.

    Self-heals orphan `tool_use` blocks. If dispatch crashed mid-turn
    in a prior session and the `tool_result` was never persisted, the
    next call to the provider would fail with "tool_use ids were found
    without tool_result blocks" and the whole conversation would be
    stuck. We stitch in a synthetic error `tool_result` for any orphan
    so the model can move on.
    """
    rows = case_chat_biz.list_messages(conversation.id)
    # Trim from the tail — keep the most recent N user turns plus their
    # associated assistant / tool exchanges. Rough heuristic: keep
    # `2 * max_turns` messages (user + assistant pair per turn) plus
    # any interleaved tool messages.
    keep = max(2, cfg.max_turns_per_conversation) * 4
    trimmed = rows[-keep:]
    out: list[ChatMessage] = []
    for row in trimmed:
        content: list[dict[str, Any]] = row.content or []
        if row.role == ROLE_TOOL:
            content, _ = redact_content_blocks(
                content,
                redact_ips=cfg.redact_ips,
                redact_emails=cfg.redact_emails,
                redact_hashes=cfg.redact_hashes,
            )
        out.append(ChatMessage(role=row.role, content=content))
    return _stitch_orphan_tool_uses(out)


def _stitch_orphan_tool_uses(
    history: list[ChatMessage],
) -> list[ChatMessage]:
    """Insert a synthetic `tool_result` after any assistant `tool_use`
    whose id isn't matched by a subsequent tool_result.

    Anthropic's API requires strict `tool_use → tool_result` pairing
    across message boundaries. A crash during dispatch — most notoriously
    the datetime-serialize bug in the emit path — could persist the
    assistant's `tool_use` without a matching `tool_result`, and every
    subsequent send() would fail with a 400 from Anthropic and the
    analyst couldn't recover without archiving. This heals it in-place
    on load.
    """
    # Walk once to collect the set of tool_result ids already present.
    have_result: set[str] = set()
    for msg in history:
        if msg.role != ROLE_TOOL:
            continue
        for b in msg.content or []:
            if isinstance(b, dict) and b.get('type') == 'tool_result':
                tu_id = b.get('tool_use_id')
                if isinstance(tu_id, str):
                    have_result.add(tu_id)

    # Now walk assistant messages and, for each tool_use with no
    # corresponding tool_result, splice a synthetic one in AFTER the
    # assistant message.
    stitched: list[ChatMessage] = []
    for msg in history:
        stitched.append(msg)
        if msg.role != ROLE_ASSISTANT:
            continue
        orphans: list[dict[str, Any]] = []
        for b in msg.content or []:
            if not (isinstance(b, dict) and b.get('type') == 'tool_use'):
                continue
            tu_id = b.get('id')
            if isinstance(tu_id, str) and tu_id not in have_result:
                orphans.append({
                    'type': 'tool_result',
                    'tool_use_id': tu_id,
                    'content': (
                        'Tool call did not complete (internal error). '
                        'Please try again or take a different approach.'
                    ),
                    'is_error': True,
                })
                have_result.add(tu_id)  # dedup if referenced twice
        if orphans:
            logger.warning(
                'chatbot: stitched %d orphan tool_use(s) in conversation history — '
                'a prior dispatch crashed without persisting tool_result',
                len(orphans),
            )
            stitched.append(ChatMessage(role=ROLE_TOOL, content=orphans))
    return stitched


def _history_was_redacted(
    history: list[ChatMessage], cfg: ChatbotConfig,
) -> bool:
    """Second pass over the history to record whether redaction ran —
    cheaper than threading a bool out of `_load_history` and lets the
    audit-row be finalised even when we short-circuited history load.
    """
    if not (cfg.redact_ips or cfg.redact_emails or cfg.redact_hashes):
        return False
    for msg in history:
        if msg.role != ROLE_TOOL:
            continue
        _, changed = redact_content_blocks(
            msg.content,
            redact_ips=cfg.redact_ips,
            redact_emails=cfg.redact_emails,
            redact_hashes=cfg.redact_hashes,
        )
        if changed:
            return True
    return False


def _prepare_tools(conversation: CaseChatConversation) -> list[ToolSpec]:
    """Build the tool list the LLM sees.

    Strips `case_identifier` from case-scoped tools' input_schema when
    the conversation itself is case-scoped, and `war_room_id` when
    war-room-scoped — the LLM shouldn't be asked to guess or supply
    them, and dispatch will override anyway.
    """
    raw = build_tools_list()
    is_case_scoped = conversation.case_id is not None
    is_war_room_scoped = conversation.war_room_id is not None
    strip_keys: list[str] = []
    if is_case_scoped:
        strip_keys.append('case_identifier')
    if is_war_room_scoped:
        strip_keys.append('war_room_id')
    out: list[ToolSpec] = []
    for entry in raw:
        schema = dict(entry.get('inputSchema') or {})
        if strip_keys:
            props = dict(schema.get('properties') or {})
            for key in strip_keys:
                props.pop(key, None)
            schema['properties'] = props
            required = [r for r in (schema.get('required') or [])
                        if r not in strip_keys]
            if required:
                schema['required'] = required
            elif 'required' in schema:
                schema.pop('required')
        out.append(ToolSpec(
            name=entry['name'],
            description=entry.get('description') or '',
            input_schema=schema,
        ))
    return out


def _estimate_request_bytes(
    system: str, history: list[ChatMessage], tools: list[ToolSpec],
) -> int:
    """Rough char-count so audit rows always have a request-size figure
    even when the provider's SDK doesn't expose it. Fast; the DPO view
    is byte-level anyway."""
    n = len(system)
    for m in history:
        n += 8 + sum(len(json.dumps(b, default=str)) for b in (m.content or []))
    for t in tools:
        n += len(t.name) + len(t.description or '')
        n += len(json.dumps(t.input_schema, default=str))
    return n


def _consume_stream(
    *,
    provider,
    model: str,
    system: str,
    history: list[ChatMessage],
    tools: list[ToolSpec],
    conversation: CaseChatConversation,
    audit_row,
    emit: Emitter,
) -> _TurnResult:
    """Iterate one provider stream. On write tool_use, persist pending
    row(s) and return early with paused_on_write=True. On end_turn,
    persist the final assistant message and return terminated=True."""

    text_buffer: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    response_bytes = 0

    for event in provider.stream_completion(
        model=model, system=system, messages=history, tools=tools,
    ):
        response_bytes += _event_bytes(event)
        if isinstance(event, TextDelta):
            text_buffer.append(event.text)
            emit(ChatEvent('assistant_delta', {'text': event.text}))
            continue
        if isinstance(event, ToolUseEnd):
            tool_uses.append({
                'id': event.tool_use_id,
                'name': event.name,
                'input': event.arguments or {},
            })
            continue
        if isinstance(event, MessageEnd):
            # Persist the final assistant message + resolve tool uses.
            case_chat_biz.finalise_egress(
                audit_row=audit_row,
                response_bytes=response_bytes,
                prompt_tokens=event.prompt_tokens,
                completion_tokens=event.completion_tokens,
            )
            return _finalise_turn(
                text=''.join(text_buffer),
                tool_uses=tool_uses,
                stop_reason=event.stop_reason,
                conversation=conversation,
                emit=emit,
            )
        if isinstance(event, Error):
            case_chat_biz.finalise_egress(
                audit_row=audit_row,
                response_bytes=response_bytes,
                prompt_tokens=None, completion_tokens=None,
            )
            emit(ChatEvent('error', {'message': event.message}))
            return _TurnResult(terminated=True)
        # ToolUseStart / ToolUseDelta — no-ops in the loop; the SPA
        # doesn't need them for reads because the final result renders
        # atomically once dispatched, and for writes the pending-row
        # notification carries the args anyway. Could surface later.

    # Stream ended without a MessageEnd — treat as error but persist
    # whatever text we captured so the analyst can see partial output.
    case_chat_biz.finalise_egress(
        audit_row=audit_row,
        response_bytes=response_bytes,
        prompt_tokens=None, completion_tokens=None,
    )
    return _finalise_turn(
        text=''.join(text_buffer),
        tool_uses=tool_uses,
        stop_reason='end_turn',
        conversation=conversation,
        emit=emit,
    )


def _finalise_turn(
    *,
    text: str,
    tool_uses: list[dict[str, Any]],
    stop_reason: str,
    conversation: CaseChatConversation,
    emit: Emitter,
) -> _TurnResult:
    """Persist the assistant message + process any tool_use blocks."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({'type': 'text', 'text': text})
    for tu in tool_uses:
        content.append({
            'type': 'tool_use',
            'id': tu['id'],
            'name': tu['name'],
            'input': tu['input'],
        })
    if not content:
        # Empty assistant turn — unusual but persist an empty text block
        # so the message history stays a valid Anthropic-shaped sequence.
        content.append({'type': 'text', 'text': ''})

    assistant_msg = case_chat_biz.append_message(
        conversation=conversation,
        role=ROLE_ASSISTANT,
        content=content,
    )

    if not tool_uses:
        emit(ChatEvent('assistant_end', {
            'message_id': assistant_msg.id,
            'stop_reason': stop_reason,
        }))
        return _TurnResult(terminated=True)

    # Classify each tool_use.
    reads_to_execute: list[dict[str, Any]] = []
    writes_to_pend: list[dict[str, Any]] = []
    for tu in tool_uses:
        name = tu['name']
        if tool_classification.is_read_only(name):
            reads_to_execute.append(tu)
        elif tool_classification.is_write(name):
            writes_to_pend.append(tu)
        else:
            # Unclassified — refuse per the safety contract.
            emit(ChatEvent('error', {
                'message': (
                    f'Tool {name!r} is not classified as read or write. '
                    'Contact your administrator — this is a code-level '
                    'gap that must be resolved before the tool can run.'
                ),
            }))
            return _TurnResult(terminated=True)

    # Auto-execute reads if allowed.
    from app.iris_engine.llm.client import load_config
    cfg = load_config()

    for tu in reads_to_execute:
        if not cfg.auto_execute_read_tools:
            # Admin opted for confirm-all — treat reads as writes.
            writes_to_pend.append(tu)
            continue
        result = _dispatch_and_track(
            conversation=conversation,
            tool_use=tu,
            emit=emit,
        )
        # Persist tool_result even on failure so the model sees the
        # error and can react — better UX than silently ignoring.
        # Anthropic requires `content` to be a string or list of
        # content blocks; a raw dict from `dispatch_tool_call` fails
        # validation. Serialize to a JSON string.
        case_chat_biz.append_message(
            conversation=conversation,
            role=ROLE_TOOL,
            content=[{
                'type': 'tool_result',
                'tool_use_id': tu['id'],
                'content': _tool_result_content(result),
            }],
            tool_use_id=tu['id'],
        )

    # Full-throttle mode: admin opted to skip the Approve/Deny card
    # for writes. Dispatch each write immediately through the same path
    # reads use (scope override, emit start/result, audit-log). The
    # `pending` flag on the event stays False so the SPA renders the
    # write as a live tool card, not an Approve card.
    if writes_to_pend and cfg.auto_approve_write_tools:
        for tu in writes_to_pend:
            result = _dispatch_and_track(
                conversation=conversation,
                tool_use=tu,
                emit=emit,
            )
            case_chat_biz.append_message(
                conversation=conversation,
                role=ROLE_TOOL,
                content=[{
                    'type': 'tool_result',
                    'tool_use_id': tu['id'],
                    'content': _tool_result_content(result),
                }],
                tool_use_id=tu['id'],
            )
        # Fall through — treat this like an all-reads turn so the caller
        # re-enters the loop with the fresh tool_result messages.
        return _TurnResult()

    # Pending writes: persist rows, emit assistant_tool_start events.
    # Apply the scope override at persist time so a tab-close-and-
    # resume-tomorrow flow doesn't lose the case scope, and so the
    # frontend Approve card has a `case_identifier` to render in the
    # diff-of-state description. `_dispatch_and_track` re-asserts this
    # on approve as a belt-and-braces safety net.
    for tu in writes_to_pend:
        raw_args = dict(tu.get('input') or {})
        if conversation.case_id is not None:
            raw_args['case_identifier'] = int(conversation.case_id)
        if conversation.war_room_id is not None:
            raw_args['war_room_id'] = int(conversation.war_room_id)
        row = case_chat_biz.create_pending_tool_call(
            conversation=conversation,
            assistant_message=assistant_msg,
            tool_use_id=tu['id'],
            tool_name=tu['name'],
            arguments=raw_args,
        )
        emit(ChatEvent('assistant_tool_start', {
            'pending_tool_call_id': row.id,
            'tool_use_id': tu['id'],
            'tool_name': tu['name'],
            'arguments': tu['input'] or {},
            'is_write': True,
            'pending': True,
        }))

    if writes_to_pend:
        return _TurnResult(paused_on_write=True)

    # All-reads turn — the caller re-enters the loop with the
    # freshly-appended tool_result messages in history.
    return _TurnResult()


def _dispatch_and_track(
    *,
    conversation: CaseChatConversation,
    tool_use: dict[str, Any],
    emit: Emitter,
) -> Any:
    """Call `dispatch_tool_call` with the case_id override applied.

    Emits `assistant_tool_start` (auto-execute) + `assistant_tool_result`
    events for the SPA to render a collapsible tool card. Never lets
    the LLM's `case_identifier` argument through — the conversation's
    scope is authoritative and a mismatch is logged as a possible
    prompt-injection tell.
    """
    name = tool_use['name']
    args = dict(tool_use.get('input') or {})

    # Scope override. See §7 of the plan.
    if conversation.case_id is not None:
        llm_case = args.get('case_identifier')
        if llm_case is not None and int(llm_case) != int(conversation.case_id):
            logger.warning(
                'chatbot: LLM emitted case_identifier=%s for a '
                'conversation scoped to case_id=%s (tool=%s, conv=%s) '
                '— overriding. Possible prompt-injection.',
                llm_case, conversation.case_id, name, conversation.id,
            )
        args['case_identifier'] = int(conversation.case_id)
    if conversation.war_room_id is not None:
        llm_wr = args.get('war_room_id')
        if llm_wr is not None and int(llm_wr) != int(conversation.war_room_id):
            logger.warning(
                'chatbot: LLM emitted war_room_id=%s for a '
                'conversation scoped to war_room_id=%s (tool=%s, conv=%s) '
                '— overriding. Possible prompt-injection.',
                llm_wr, conversation.war_room_id, name, conversation.id,
            )
        args['war_room_id'] = int(conversation.war_room_id)

    emit(ChatEvent('assistant_tool_start', {
        'tool_use_id': tool_use['id'],
        'tool_name': name,
        'arguments': args,
        'is_write': tool_classification.is_write(name),
        'pending': False,
    }))

    try:
        result = dispatch_tool_call(name, args)
    except MCPError as exc:
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'error': exc.message,
        }))
        return {'error': exc.message}
    except Exception as exc:
        logger.exception('chatbot: dispatch failed for %s', name)
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'error': f'Internal error: {exc}',
        }))
        return {'error': str(exc)}

    # Audit — every chatbot-driven tool call also hits track_activity
    # so the case activity log shows what the assistant did.
    caseid = conversation.case_id
    try:
        track_activity(
            f'chat:{name} tool_use_id={tool_use["id"]}',
            caseid=caseid,
            ctx_less=caseid is None,
            display_in_ui=caseid is not None,
        )
    except Exception:
        logger.exception('chatbot: track_activity failed for %s', name)

    # Emit is best-effort — if it fails (JSON encoding bug, socket
    # dropped mid-turn, etc.), we still return `result` so the caller
    # persists the tool_result message. Losing that persistence would
    # leave an orphan tool_use in history and Anthropic would reject
    # the next request with "tool_use ids were found without
    # tool_result blocks".
    try:
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': tool_use['id'],
            'tool_name': name,
            'result': result,
        }))
    except Exception:
        logger.exception('chatbot: assistant_tool_result emit failed for %s', name)
    return result


def _event_bytes(event: LLMEvent) -> int:
    """Rough byte count of a single event for the audit row's
    response_bytes tally. Cheap; the DPO view doesn't need
    protocol-level precision."""
    if isinstance(event, TextDelta):
        return len(event.text)
    if isinstance(event, ToolUseEnd):
        return len(event.name) + len(json.dumps(event.arguments, default=str))
    return 0


# Re-export so approve_tool.py can call the loop from its handler
# without a circular import through the namespace module.
__all__ = ['run_one_iteration', 'ChatEvent', 'Emitter']

# Silence "imported but unused" — kept in case an unclassified tool
# escapes classification at runtime and we need to re-raise.
_ = UnclassifiedToolError
