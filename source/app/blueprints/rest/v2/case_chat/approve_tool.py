#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Resolve a pending write tool_call: approve → dispatch + append + re-run.

Called from the `approve_tool` / `deny_tool` SocketIO event handlers.
Uses `SELECT ... FOR UPDATE` (inside `resolve_pending_tool_call`) so a
double-click on Approve can't dispatch twice.

Deny is symmetric: mark the row denied, append a synthetic
`tool_result: 'User denied this action.'` so the LLM sees the outcome,
then re-run the loop.
"""
from __future__ import annotations

import logging
from typing import Any

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.case_chat import tool_classification
from app.blueprints.rest.v2.case_chat.loop import (
    ChatEvent,
    Emitter,
    run_one_iteration,
)
from app.blueprints.rest.v2.mcp.dispatch import MCPError, dispatch_tool_call
from app.business import case_chat as case_chat_biz
from app.iris_engine.utils.tracker import track_activity
from app.models.case_chat import ROLE_TOOL
from app.models.errors import BusinessProcessingError, ObjectNotFoundError


logger = logging.getLogger('iris.chatbot.approve')


def approve_pending_tool_call(
    *,
    pending_tool_call_id: int,
    emit: Emitter,
) -> None:
    """Mark approved, dispatch, append tool_result, re-run one loop
    iteration. All error paths emit an `error` event to the client
    and return — the caller (namespace handler) doesn't need to catch."""
    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row, transitioned = case_chat_biz.resolve_pending_tool_call(
            pending_id=pending_tool_call_id, user=user, approve=True,
        )
    except ObjectNotFoundError:
        emit(ChatEvent('error', {'message': 'Pending tool call not found.'}))
        return
    except BusinessProcessingError as exc:
        emit(ChatEvent('error', {'message': exc.get_message()}))
        return

    if not transitioned:
        # Race lost — another approve already ran dispatch + persisted
        # the tool_result. Doing it again would duplicate the row in
        # history and Anthropic would 400 the next request.
        return

    conv = case_chat_biz.get_conversation(row.conversation_id)

    # Scope override — the LLM-supplied args may have been redirected.
    args: dict[str, Any] = dict(row.arguments or {})
    if conv.case_id is not None:
        llm_case = args.get('case_identifier')
        if llm_case is not None and int(llm_case) != int(conv.case_id):
            logger.warning(
                'chatbot approve: LLM emitted case_identifier=%s for '
                'a conversation scoped to case=%s (tool=%s, conv=%s) '
                '— overriding.',
                llm_case, conv.case_id, row.tool_name, conv.id,
            )
        args['case_identifier'] = int(conv.case_id)

    emit(ChatEvent('assistant_tool_start', {
        'tool_use_id': row.tool_use_id,
        'tool_name': row.tool_name,
        'arguments': args,
        'is_write': tool_classification.is_write(row.tool_name),
        'pending': False,
    }))

    try:
        result: Any = dispatch_tool_call(row.tool_name, args)
    except MCPError as exc:
        result = {'error': exc.message}
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': row.tool_use_id,
            'tool_name': row.tool_name,
            'error': exc.message,
        }))
    except Exception as exc:
        logger.exception('chatbot: dispatch failed on approve for %s', row.tool_name)
        result = {'error': str(exc)}
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': row.tool_use_id,
            'tool_name': row.tool_name,
            'error': str(exc),
        }))
    else:
        try:
            track_activity(
                f'chat:{row.tool_name} tool_use_id={row.tool_use_id}',
                caseid=conv.case_id,
                ctx_less=conv.case_id is None,
                display_in_ui=conv.case_id is not None,
            )
        except Exception:
            logger.exception('chatbot: track_activity failed on approve')
        emit(ChatEvent('assistant_tool_result', {
            'tool_use_id': row.tool_use_id,
            'tool_name': row.tool_name,
            'result': result,
        }))

    # Anthropic requires `tool_result.content` to be a string or list
    # of content blocks — a raw dict from `dispatch_tool_call` would be
    # rejected. Route through the same coercion the read-path uses.
    from app.blueprints.rest.v2.case_chat.loop import _tool_result_content
    case_chat_biz.append_message(
        conversation=conv,
        role=ROLE_TOOL,
        content=[{
            'type': 'tool_result',
            'tool_use_id': row.tool_use_id,
            'content': _tool_result_content(result),
        }],
        tool_use_id=row.tool_use_id,
    )

    # If any other pending rows on the same assistant message are
    # still pending, don't advance the loop — wait for them.
    remaining = case_chat_biz.count_pending_tool_calls(
        conv.id, row.assistant_message_id,
    )
    if remaining > 0:
        return

    run_one_iteration(conversation=conv, emit=emit)


def deny_pending_tool_call(
    *,
    pending_tool_call_id: int,
    emit: Emitter,
) -> None:
    """Mark denied, append synthetic 'User denied this action.' tool_result,
    re-run the loop so the LLM can react (typically by moving on)."""
    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row, transitioned = case_chat_biz.resolve_pending_tool_call(
            pending_id=pending_tool_call_id, user=user, approve=False,
        )
    except ObjectNotFoundError:
        emit(ChatEvent('error', {'message': 'Pending tool call not found.'}))
        return
    except BusinessProcessingError as exc:
        emit(ChatEvent('error', {'message': exc.get_message()}))
        return

    if not transitioned:
        # Race lost — the row was already resolved. Same guard as
        # approve: don't append a second tool_result.
        return

    conv = case_chat_biz.get_conversation(row.conversation_id)

    emit(ChatEvent('assistant_tool_result', {
        'tool_use_id': row.tool_use_id,
        'tool_name': row.tool_name,
        'result': {'denied': True, 'message': 'User denied this action.'},
    }))

    case_chat_biz.append_message(
        conversation=conv,
        role=ROLE_TOOL,
        content=[{
            'type': 'tool_result',
            'tool_use_id': row.tool_use_id,
            'content': 'User denied this action.',
        }],
        tool_use_id=row.tool_use_id,
    )

    remaining = case_chat_biz.count_pending_tool_calls(
        conv.id, row.assistant_message_id,
    )
    if remaining > 0:
        return

    run_one_iteration(conversation=conv, emit=emit)
