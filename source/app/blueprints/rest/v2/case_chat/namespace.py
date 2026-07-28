#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""SocketIO `/chat` namespace: send / approve / deny events.

Mirrors the class-based Namespace pattern from `AlertsNamespace` in
`app/__init__.py` and the decorator-based auth-at-connect pattern from
`notification_event_handlers`. Each event handler runs inside a Flask
request context (Flask-SocketIO wraps them), so `iris_current_user` is
populated and `dispatch_tool_call` works without a manual
`test_request_context` wrapper.

Under gevent, the streaming LLM read inside `run_one_iteration` yields
cooperatively between tokens — one worker can service many concurrent
chat sockets.
"""
from __future__ import annotations

import logging
from typing import Any

from flask import g, request
from flask_socketio import Namespace, emit, join_room, leave_room

from app.blueprints.access_controls import (
    ac_fast_check_current_user_has_case_access,
    is_user_authenticated,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.case_chat.approve_tool import (
    approve_pending_tool_call,
    deny_pending_tool_call,
)
from app.blueprints.rest.v2.case_chat.loop import ChatEvent, run_one_iteration
from app.business import case_chat as case_chat_biz
from app.business.auth import validate_auth_token
from app.models.authorization import CaseAccessLevel
from app.models.case_chat import ROLE_USER
from app.models.errors import ObjectNotFoundError


logger = logging.getLogger('iris.chatbot.namespace')

NAMESPACE = '/chat'

# Same in-memory sid → user_id mapping the notifications namespace uses.
# Persists across events on the same socket so we can identify the
# caller even when Flask-SocketIO's per-event request context doesn't
# carry `g.auth_user` (JWT-only auth path).
_sid_user_ids: dict[str, int] = {}

# Per-user open-connection cap (§7 safety-g). Blunts a runaway loop or
# malicious script that pops N sockets to burn LLM tokens.
_MAX_SOCKETS_PER_USER = 3
_sid_by_user: dict[int, set[str]] = {}


def _current_user_id() -> int | None:
    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    if user is not None:
        uid = getattr(user, 'id', None)
        if uid:
            return uid
    return _sid_user_ids.get(getattr(request, 'sid', None))


class ChatNamespace(Namespace):
    """Class-based namespace for the chatbot's send/approve/deny flow."""

    # --- connection lifecycle ---------------------------------------

    def on_connect(self, auth: Any | None = None) -> bool | None:
        """Reject unauthenticated / over-cap connections at handshake.

        Same auth resolution as `/notifications` (session first, then
        `auth.token` fallback). Additionally enforces
        `_MAX_SOCKETS_PER_USER` — a fourth concurrent chat socket for
        the same user is refused.
        """
        user_id: int | None = None
        if is_user_authenticated(request):
            user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
            user_id = getattr(user_obj, 'id', None) if user_obj else None

        if user_id is None and isinstance(auth, dict):
            token = auth.get('token')
            if isinstance(token, str) and token:
                user_data = validate_auth_token(token)
                if user_data and not (
                    user_data.get('mfa_required')
                    and not user_data.get('mfa_verified')
                ):
                    g.auth_user = user_data
                    g.auth_token_user_id = user_data['user_id']
                    user_id = user_data['user_id']

        if not user_id:
            return False

        open_for_user = _sid_by_user.setdefault(user_id, set())
        if len(open_for_user) >= _MAX_SOCKETS_PER_USER:
            logger.info(
                'chatbot: user %s hit per-user socket cap of %s',
                user_id, _MAX_SOCKETS_PER_USER,
            )
            return False

        _sid_user_ids[request.sid] = user_id
        open_for_user.add(request.sid)
        # Per-user room lets the server broadcast to every open panel
        # for the same user (e.g. mark a conversation as resolved).
        join_room(f'user-{user_id}')
        return True

    def on_disconnect(self, reason: Any = None) -> None:
        sid = request.sid
        user_id = _sid_user_ids.pop(sid, None)
        if user_id is not None:
            sids = _sid_by_user.get(user_id)
            if sids is not None:
                sids.discard(sid)
                if not sids:
                    _sid_by_user.pop(user_id, None)

    # --- room join for a specific conversation ---------------------

    def on_join_conversation(self, data: Any) -> None:
        """Client → server: subscribe this sid to per-conversation events.

        Emits from the loop use `to=f'conv-{id}'` so multiple panels on
        the same account (say the analyst has the app open in two tabs)
        both see streaming deltas. Access is verified here — you can
        only join a conversation you own.
        """
        conversation_id = _int(data, 'conversation_id')
        if conversation_id is None:
            emit('error', {'message': 'conversation_id is required'})
            return
        try:
            conv = case_chat_biz.get_conversation(conversation_id)
        except ObjectNotFoundError:
            emit('error', {'message': 'Conversation not found.'})
            return
        if conv.user_id != _current_user_id():
            emit('error', {'message': 'This conversation belongs to another user.'})
            return
        if conv.case_id is not None:
            if not ac_fast_check_current_user_has_case_access(
                    conv.case_id,
                    [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
                emit('error', {'message': f'No access to case #{conv.case_id}.'})
                return
        join_room(f'conv-{conv.id}')
        emit('joined', {'conversation_id': conv.id})

    def on_leave_conversation(self, data: Any) -> None:
        conversation_id = _int(data, 'conversation_id')
        if conversation_id is not None:
            leave_room(f'conv-{conversation_id}')

    # --- send a user turn ------------------------------------------

    def on_send(self, data: Any) -> None:
        """Client → server: append a user turn, run one loop iteration."""
        conversation_id = _int(data, 'conversation_id')
        user_text = (data or {}).get('user_text') if isinstance(data, dict) else None
        if conversation_id is None or not isinstance(user_text, str) or not user_text.strip():
            emit('error', {'message': 'conversation_id + user_text are required'})
            return

        try:
            conv = case_chat_biz.get_conversation(conversation_id)
        except ObjectNotFoundError:
            emit('error', {'message': 'Conversation not found.'})
            return
        user_id = _current_user_id()
        if user_id is None or conv.user_id != user_id:
            emit('error', {'message': 'This conversation belongs to another user.'})
            return
        if conv.case_id is not None:
            if not ac_fast_check_current_user_has_case_access(
                    conv.case_id,
                    [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
                emit('error', {'message': f'No access to case #{conv.case_id}.'})
                return

        text = user_text.strip()
        case_chat_biz.append_message(
            conversation=conv,
            role=ROLE_USER,
            content=[{'type': 'text', 'text': text}],
        )
        # First user turn seeds the conversation title.
        if not conv.title:
            from app.db import db
            conv.title = text[:80]
            db.session.commit()

        emit('user_message_persisted', {'conversation_id': conv.id})

        run_one_iteration(conversation=conv, emit=_emit_for_conv(conv.id))

    # --- resolve pending tool calls --------------------------------

    def on_approve_tool(self, data: Any) -> None:
        pending_id = _int(data, 'pending_tool_call_id')
        if pending_id is None:
            emit('error', {'message': 'pending_tool_call_id is required'})
            return
        conv_id = _int(data, 'conversation_id')
        approve_pending_tool_call(
            pending_tool_call_id=pending_id,
            emit=_emit_for_conv(conv_id) if conv_id else _emit_direct(),
        )

    def on_deny_tool(self, data: Any) -> None:
        pending_id = _int(data, 'pending_tool_call_id')
        if pending_id is None:
            emit('error', {'message': 'pending_tool_call_id is required'})
            return
        conv_id = _int(data, 'conversation_id')
        deny_pending_tool_call(
            pending_tool_call_id=pending_id,
            emit=_emit_for_conv(conv_id) if conv_id else _emit_direct(),
        )


# ---- Helpers -------------------------------------------------------

def _int(data: Any, key: str) -> int | None:
    if not isinstance(data, dict):
        return None
    v = data.get(key)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _emit_for_conv(conversation_id: int):
    """Return an `Emitter` bound to a per-conversation room, so every
    panel the analyst has open on this conversation sees the stream."""
    def _emit(event: ChatEvent) -> None:
        # Late import to avoid a cycle at module init.
        from app import socket_io
        socket_io.emit(
            event.name,
            {'conversation_id': conversation_id, **event.payload},
            namespace=NAMESPACE,
            to=f'conv-{conversation_id}',
        )
    return _emit


def _emit_direct():
    """Emit back to the caller's sid only. Used for pre-conversation
    errors (bad payload, wrong user) where no conversation room exists."""
    def _emit(event: ChatEvent) -> None:
        emit(event.name, event.payload)
    return _emit


def register_chat_namespace() -> None:
    """Called from `app.__init__` to bind the namespace instance.

    The class body doesn't self-register — SocketIO namespaces are
    bound via `socket_io.on_namespace(ChatNamespace('/chat'))`.
    """
    from app import socket_io
    socket_io.on_namespace(ChatNamespace(NAMESPACE))
