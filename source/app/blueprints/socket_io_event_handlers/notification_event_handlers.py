#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""SocketIO namespace for user-scoped notifications.

Clients open `/notifications` and emit `join` (no payload) to be added
to their own `user-<self.id>` room. That room name is derived entirely
from the authenticated session — the client CANNOT ask to join another
user's room. Server-emitted `new_notification` events are addressed to
`user-<recipient>` rooms only, so cross-user leakage requires either a
session-fixation bug elsewhere in the app OR joining someone else's
room; the latter is what this handler prevents.
"""

import logging
import time
import uuid

from flask import g, request
from flask_socketio import emit
from flask_socketio import join_room
from flask_socketio import leave_room
from flask_socketio import rooms

from app import socket_io
from app.blueprints.access_controls import is_user_authenticated
from app.blueprints.iris_user import iris_current_user
from app.business.ai_workflows import ai_workflows_editor_session
from app.business.ai_workflows import ai_workflows_live_room
from app.business.auth import validate_auth_token
from app.business.case_graph_live import case_graph_live_join_room
from app.business.case_graph_live import case_graph_live_presence_user
from app.business.war_rooms_live import war_rooms_live_join_room
from app.iris_engine.ai_workflows.live import EDITOR_EVENT
from app.iris_engine.ai_workflows.live import ai_workflows_live_run_room
from app.iris_engine.ai_workflows.live import ai_workflows_live_workflow_room
from app.iris_engine.case_graph_live import CASE_GRAPH_LIVE_NAMESPACES
from app.iris_engine.case_graph_live import CASE_GRAPH_PRESENCE_EVENT
from app.iris_engine.case_graph_live import case_graph_live_room
from app.iris_engine.war_rooms_live import war_rooms_live_room


logger = logging.getLogger(__name__)


NAMESPACE = '/notifications'


# `g.auth_user` set by the auth-payload fallback in `on_connect` doesn't
# survive to subsequent events (each event gets its own request context),
# so we mirror the authenticated user id per-sid. See `_current_user_id`.
_sid_user_ids: dict[str, int] = {}


def _current_user_id():
    """Return the current authenticated user's id, or None."""
    # `iris_current_user` is a LocalProxy — resolve to the underlying
    # user object before touching attributes so we don't blow up on
    # unauthenticated sessions.
    user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    if user is not None:
        uid = getattr(user, 'id', None)
        if uid:
            return uid
    return _sid_user_ids.get(getattr(request, 'sid', None))


@socket_io.on('connect', namespace=NAMESPACE)
def on_connect(auth):
    """Require auth at connect time so unauthenticated sockets never
    persist in the namespace. Returning False rejects the connection.

    Auth resolution: session/header first, then fall back to the token
    supplied via socket.io's `auth` handshake payload — the browser
    strips custom headers on WS upgrade so a header-only path breaks
    behind SSO proxies. Matches the pattern used by the `/collab`
    namespace; see `collab_event_handlers.on_connect` for the writeup.

    We **auto-join** the caller's own user room here as part of the
    connect handshake — waiting for a follow-up `emit('join')` from
    the client left a race window (a few hundred ms on WebSocket, up
    to a full long-poll cycle on polling transport) where server-side
    emits landed in an empty room and were silently dropped. Since
    the room name is derived entirely from the authenticated session,
    the auto-join can't be abused to eavesdrop on someone else.
    """
    user_id = None

    if is_user_authenticated(request):
        user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
        user_id = getattr(user_obj, 'id', None) if user_obj else None

    if user_id is None and isinstance(auth, dict):
        token = auth.get('token')
        if isinstance(token, str) and token:
            user_data = validate_auth_token(token)
            if user_data and not (
                user_data.get('mfa_required') and not user_data.get('mfa_verified')
            ):
                g.auth_user = user_data
                g.auth_token_user_id = user_data['user_id']
                user_id = user_data['user_id']

    if not user_id:
        return False

    _sid_user_ids[request.sid] = user_id
    join_room(f'user-{user_id}')
    logger.debug('notification namespace: user-%s joined on connect', user_id)
    return True


@socket_io.on('disconnect', namespace=NAMESPACE)
def on_disconnect():
    """Drop the per-sid identity so we don't leak entries as sockets
    churn. Room membership is cleaned up by flask-socketio itself.
    """
    _sid_user_ids.pop(request.sid, None)
    for entry in (_editor_sessions.pop(request.sid, None) or {}).values():
        _editor_broadcast('gone', entry)


@socket_io.on('join', namespace=NAMESPACE)
def on_join(_data=None):
    """Legacy join handler — kept for older clients that still emit
    `join` explicitly after connect. Idempotent: the connect handler
    already joined the room, so this is now a no-op ack.

    The `_data` payload is ignored entirely — the room is derived
    from the session id, NOT the payload.
    """
    user_id = _current_user_id()
    if not user_id:
        return
    # Idempotent — flask-socketio's join_room is a no-op when the
    # session is already in the room.
    join_room(f'user-{user_id}')
    emit('joined', {'ok': True, 'user_id': user_id})


@socket_io.on('leave', namespace=NAMESPACE)
def on_leave(_data=None):
    user_id = _current_user_id()
    if not user_id:
        return
    leave_room(f'user-{user_id}')


def _live_room(data):
    """Room of the run / workflow named in `data` the current user may
    watch, or None. The access check is the REST one."""
    user_id = _current_user_id()
    if not user_id or not isinstance(data, dict):
        return None
    run_uuid = data.get('run_uuid')
    workflow_id = data.get('workflow_id')
    if not isinstance(run_uuid, (str, type(None))) or isinstance(workflow_id, bool) \
            or not isinstance(workflow_id, (int, type(None))):
        return None
    try:
        return ai_workflows_live_room(user_id, run_uuid=run_uuid, workflow_id=workflow_id)
    except Exception:
        return None


@socket_io.on('ai_workflow_watch', namespace=NAMESPACE)
def on_ai_workflow_watch(data=None):
    """Follow the live progress of an AI workflow run (`{run_uuid}`) or
    of every run of a workflow (`{workflow_id}`): `ai_workflow_run`
    events. The ack tells whether the room was joined."""
    room = _live_room(data)
    if room is None:
        return {'ok': False}
    join_room(room)
    return {'ok': True}


@socket_io.on('ai_workflow_unwatch', namespace=NAMESPACE)
def on_ai_workflow_unwatch(data=None):
    """Leaving needs no access check (nor still having access)."""
    if not isinstance(data, dict):
        return
    run_uuid = data.get('run_uuid')
    workflow_id = data.get('workflow_id')
    if isinstance(run_uuid, str) and run_uuid:
        leave_room(ai_workflows_live_run_room(run_uuid[:64]))
    if isinstance(workflow_id, int) and not isinstance(workflow_id, bool):
        leave_room(ai_workflows_live_workflow_room(workflow_id))


# ---- War-room live views ---------------------------------------------------

@socket_io.on('war_room_watch', namespace=NAMESPACE)
def on_war_room_watch(data=None):
    """Follow the changes of a war room (`{war_room_id}`) the user may
    read: `war_room_changed` events. The ack tells whether the room was
    joined."""
    user_id = _current_user_id()
    if not user_id or not isinstance(data, dict):
        return {'ok': False}
    try:
        room = war_rooms_live_join_room(user_id, data.get('war_room_id'))
    except Exception:
        return {'ok': False}
    join_room(room)
    return {'ok': True}


@socket_io.on('war_room_unwatch', namespace=NAMESPACE)
def on_war_room_unwatch(data=None):
    """Leaving needs no access check (nor still having access)."""
    if not isinstance(data, dict):
        return
    war_room_id = data.get('war_room_id')
    if isinstance(war_room_id, int) and not isinstance(war_room_id, bool) and war_room_id > 0:
        leave_room(war_rooms_live_room(war_room_id))


# ---- AI workflow editor presence -------------------------------------------
# Who has the editor of a workflow open, and which node each one is
# configuring. Nothing is kept for the room as a whole: the gunicorn
# workers don't share memory, while a socket's events always reach the
# same worker. So each socket's entry lives with it, every change is
# relayed to the room, and the clients build the list of editors from
# what they receive — a `hello` makes the others announce themselves,
# a heartbeat (`ai_workflow_editor_update`) keeps an entry alive, and
# `gone` (a leave or a disconnect) removes it. Identity, the peer id and
# the time a node was focused are set here, never taken from the client.

_editor_sessions: dict[str, dict[int, dict]] = {}
_MAX_EDITOR_ROOMS_PER_SOCKET = 8
_MAX_NODE_ID_LEN = 128


def _editor_broadcast(kind, entry):
    payload = {'type': kind, 'workflow_id': entry['workflow_id'], 'peer_id': entry['peer_id'],
               'user_id': entry['user_id'], 'user_name': entry['user_name'], 'node_id': entry['node_id'],
               'focused_at': entry['focused_at']}
    try:
        socket_io.emit(EDITOR_EVENT, payload, room=entry['room'], skip_sid=request.sid, namespace=NAMESPACE)
    except Exception as exc:
        logger.warning('SocketIO emit failed for %s: %s', EDITOR_EVENT, exc)


def _editor_args(data):
    """(workflow_id, node_id) of an editor event, or None when malformed."""
    if not isinstance(data, dict):
        return None
    workflow_id = data.get('workflow_id')
    node_id = data.get('node_id')
    if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
        return None
    if node_id is not None and (not isinstance(node_id, str) or not 0 < len(node_id) <= _MAX_NODE_ID_LEN):
        return None
    return workflow_id, node_id


def _editor_ack(entry):
    return {'ok': True, 'peer_id': entry['peer_id'], 'node_id': entry['node_id'], 'focused_at': entry['focused_at']}


def _editor_focus(entry, node_id):
    if entry['node_id'] != node_id:
        entry['node_id'] = node_id
        entry['focused_at'] = time.time() if node_id else None


@socket_io.on('ai_workflow_editor_join', namespace=NAMESPACE)
def on_ai_workflow_editor_join(data=None):
    """Open the editor of a workflow (`{workflow_id, node_id}`): join its
    editors' room, announce yourself (`hello`), receive the others'
    `ai_workflow_editor` and `ai_workflow_saved` events. The ack carries
    this tab's peer id. Joining again (a reconnection) re-announces."""
    args = _editor_args(data)
    user_id = _current_user_id()
    if args is None or not user_id:
        return {'ok': False}
    workflow_id, node_id = args
    sessions = _editor_sessions.setdefault(request.sid, {})
    entry = sessions.get(workflow_id)
    if entry is None and len(sessions) >= _MAX_EDITOR_ROOMS_PER_SOCKET:
        return {'ok': False}
    try:
        session = ai_workflows_editor_session(user_id, workflow_id)
    except Exception:
        return {'ok': False}
    if entry is None:
        entry = {'peer_id': uuid.uuid4().hex, 'node_id': None, 'focused_at': None}
        sessions[workflow_id] = entry
    entry.update(room=session['room'], workflow_id=session['workflow_id'], user_id=session['user_id'],
                 user_name=session['user_name'])
    _editor_focus(entry, node_id)
    join_room(entry['room'])
    _editor_broadcast('hello', entry)
    return _editor_ack(entry)


@socket_io.on('ai_workflow_editor_update', namespace=NAMESPACE)
def on_ai_workflow_editor_update(data=None):
    """The node this tab configures (`{workflow_id, node_id}`, null for
    none), relayed as `here`; sent again as the heartbeat and in answer
    to a `hello`. Only for a workflow joined first."""
    args = _editor_args(data)
    if args is None:
        return {'ok': False}
    workflow_id, node_id = args
    entry = _editor_sessions.get(request.sid, {}).get(workflow_id)
    if entry is None:
        return {'ok': False}
    _editor_focus(entry, node_id)
    _editor_broadcast('here', entry)
    return _editor_ack(entry)


@socket_io.on('ai_workflow_editor_leave', namespace=NAMESPACE)
def on_ai_workflow_editor_leave(data=None):
    if not isinstance(data, dict):
        return
    workflow_id = data.get('workflow_id')
    if isinstance(workflow_id, bool) or not isinstance(workflow_id, int):
        return
    sessions = _editor_sessions.get(request.sid, {})
    entry = sessions.pop(workflow_id, None)
    if not sessions:
        _editor_sessions.pop(request.sid, None)
    if entry is None:
        return
    _editor_broadcast('gone', entry)
    leave_room(entry['room'])


# ---- Case graph live views -------------------------------------------------
# Registered in `/notifications` (the authenticated namespace the SPA
# keeps open) and in the default namespace, where `case-graph-changed`
# is emitted too (see `app.iris_engine.case_graph_live`).

_PRESENCE_KEY_MAX_LEN = 256


def _case_graph_case_id(data):
    if not isinstance(data, dict):
        return None
    case_id = data.get('case_id')
    if isinstance(case_id, bool) or not isinstance(case_id, int) or case_id <= 0:
        return None
    return case_id


def on_case_graph_join(data=None):
    """Follow the changes of the graph of a case (`{case_id}`) the user
    may read: `case-graph-changed` and `case-graph-presence` events. The
    ack tells whether the room was joined."""
    user_id = _current_user_id()
    case_id = _case_graph_case_id(data)
    if not user_id or case_id is None:
        return {'ok': False}
    try:
        room = case_graph_live_join_room(user_id, case_id)
    except Exception:
        return {'ok': False}
    join_room(room)
    return {'ok': True}


def on_case_graph_leave(data=None):
    """Leaving needs no access check (nor still having access)."""
    case_id = _case_graph_case_id(data)
    if case_id is None:
        return
    leave_room(case_graph_live_room(case_id))


def on_case_graph_presence(data=None):
    """Relay `{case_id, selected_key}` to the other viewers of a graph
    joined first, as `case-graph-presence {case_id, user, selected_key}`
    (the user is set here, never taken from the client)."""
    user_id = _current_user_id()
    case_id = _case_graph_case_id(data)
    if not user_id or case_id is None:
        return {'ok': False}
    room = case_graph_live_room(case_id)
    if room not in rooms():
        return {'ok': False}
    selected_key = data.get('selected_key')
    if selected_key is not None and (not isinstance(selected_key, str)
                                     or len(selected_key) > _PRESENCE_KEY_MAX_LEN):
        return {'ok': False}
    try:
        user = case_graph_live_presence_user(user_id)
    except Exception:
        return {'ok': False}
    payload = {'case_id': case_id, 'user': user, 'selected_key': selected_key}
    try:
        emit(CASE_GRAPH_PRESENCE_EVENT, payload, to=room, skip_sid=request.sid)
    except Exception as exc:
        logger.warning('SocketIO emit failed for %s: %s', CASE_GRAPH_PRESENCE_EVENT, exc)
    return {'ok': True}


for _namespace in CASE_GRAPH_LIVE_NAMESPACES:
    socket_io.on_event('join-case-graph', on_case_graph_join, namespace=_namespace)
    socket_io.on_event('leave-case-graph', on_case_graph_leave, namespace=_namespace)
    socket_io.on_event(CASE_GRAPH_PRESENCE_EVENT, on_case_graph_presence, namespace=_namespace)


def register_notification_socket_handlers():
    """Called from app __init__ after the other socket handlers.

    The `@socket_io.on` decorators above register at import time, so
    this function is a no-op body — its purpose is to force the module
    to be imported (which triggers those decorators). Mirrors the
    pattern used by the case / notes / update handlers.
    """
    return
