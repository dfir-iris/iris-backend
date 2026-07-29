#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS

"""MCP tools for the War Rooms domain.

Yuki (the chatbot) uses these to read and mutate war-rooms when the
conversation is scoped to one. `war_room_scoped=True` on the decorator
adds a `war_room_id` slot to each tool's input schema, and dispatch
enforces access before invocation — the loop also overrides the id
with the conversation's scope on every call (see loop.py §7 for the
prompt-injection defence rationale).

Only read + creation tools are exposed in v1. Delete / archive is
deliberately excluded — the analyst can perform those from the UI,
and letting an LLM propose them would be a common footgun.
"""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business import (
    war_room_chat as war_room_chat_biz,
    war_room_notes as war_room_notes_biz,
    war_room_sitreps as war_room_sitreps_biz,
    war_room_tasks as war_room_tasks_biz,
    war_rooms as war_rooms_biz,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError


# ---- War-room top-level --------------------------------------------

@mcp_tool(
    name='iris_war_rooms_list',
    description='List war rooms the current user has access to.',
    input_schema={
        'type': 'object',
        'properties': {
            'state': {
                'type': 'string',
                'description': "Filter by state (e.g. 'open', 'closed').",
            },
            'search': {
                'type': 'string',
                'description': 'Free-text match on name / description.',
            },
        },
    },
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_war_rooms_list(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        rooms = war_rooms_biz.war_room_list_for_user(
            user_id=user_obj.id,
            is_admin=False,
            state=args.get('state'),
            search=args.get('search'),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'war_rooms': [_room_summary(r) for r in rooms]}


@mcp_tool(
    name='iris_war_rooms_get',
    description='Fetch a war room by id.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_rooms_get(args: dict) -> dict:
    try:
        room = war_rooms_biz.war_room_get(args['war_room_id'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'War room not found.') from exc
    return _room_summary(room)


# ---- Chat ----------------------------------------------------------

@mcp_tool(
    name='iris_war_room_chat_list',
    description=(
        'List the latest messages in a war room stream. Merges chat rows '
        'with the attached cases\' UserActivity — same view the UI shows.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'limit': {
                'type': 'integer',
                'description': 'Max messages to return (default 50).',
            },
            'search': {
                'type': 'string',
                'description': 'Free-text substring match on message body.',
            },
        },
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_chat_list(args: dict) -> dict:
    try:
        rows = war_room_chat_biz.list_messages(
            war_room_id=args['war_room_id'],
            limit=args.get('limit') or 50,
            search=args.get('search'),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'messages': [_chat_row(m) for m in rows]}


@mcp_tool(
    name='iris_war_room_chat_post',
    description=(
        'Post a chat message to a war room. Plain text body; markdown '
        'is rendered by the client. Never attach files this way — file '
        'attachments require the drag-drop UI path.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'body': {
                'type': 'string',
                'minLength': 1,
                'description': 'Message text. Markdown accepted.',
            },
        },
        'required': ['body'],
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_chat_post(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_chat_biz.create_message(
            war_room_id=args['war_room_id'],
            author_id=user_obj.id,
            body=args['body'],
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _chat_row(row)


# ---- Sitreps -------------------------------------------------------

@mcp_tool(
    name='iris_war_room_sitreps_list',
    description='List sitreps in a war room, newest first.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_sitreps_list(args: dict) -> dict:
    rows = war_room_sitreps_biz.sitrep_list(args['war_room_id'])
    return {'sitreps': [_sitrep(s) for s in rows]}


@mcp_tool(
    name='iris_war_room_sitreps_create',
    description=(
        'Draft a new sitrep. Body accepts markdown. Draft only — an '
        'analyst still has to publish it from the UI.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'body_md': {'type': 'string'},
        },
        'required': ['title'],
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_sitreps_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_sitreps_biz.sitrep_draft(
            war_room_id=args['war_room_id'],
            title=args['title'],
            body_md=args.get('body_md') or '',
            authored_by_id=user_obj.id,
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return _sitrep(row)


# ---- Notes ---------------------------------------------------------

@mcp_tool(
    name='iris_war_room_notes_list',
    description='List notes in a war room.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_list(args: dict) -> dict:
    rows = war_room_notes_biz.war_room_note_list(args['war_room_id'])
    return {'notes': [_note(n) for n in rows]}


@mcp_tool(
    name='iris_war_room_notes_create',
    description='Create a note in a war room. Body accepts markdown.',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'content': {'type': 'string'},
        },
        'required': ['title'],
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_notes_biz.war_room_note_create(
            war_room_id=args['war_room_id'],
            title=args['title'],
            content=args.get('content') or '',
            created_by_id=user_obj.id,
        )
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    return _note(row)


# ---- Tasks ---------------------------------------------------------

@mcp_tool(
    name='iris_war_room_tasks_list',
    description='List tasks in a war room.',
    input_schema={
        'type': 'object',
        'properties': {
            'q': {'type': 'string', 'description': 'Free-text search.'},
        },
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_list(args: dict) -> dict:
    rows = war_room_tasks_biz.war_room_task_list(
        war_room_id=args['war_room_id'],
        q=args.get('q'),
    )
    return {'tasks': [_task(t) for t in rows]}


@mcp_tool(
    name='iris_war_room_tasks_create',
    description='Create a task in a war room.',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'description': {'type': 'string'},
        },
        'required': ['title'],
    },
    permissions=(Permissions.standard_user,),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_tasks_biz.war_room_task_create(
            war_room_id=args['war_room_id'],
            title=args['title'],
            description=args.get('description'),
            created_by_id=user_obj.id,
        )
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    return _task(row)


# ---- Serializers (kept local so we don't leak SQLAlchemy shapes) ---

def _room_summary(r) -> dict:
    return {
        'war_room_id': getattr(r, 'war_room_id', None),
        'name': getattr(r, 'name', None),
        'description': getattr(r, 'description', None),
        'state': getattr(r, 'state', None),
        'severity_id': getattr(r, 'severity_id', None),
        'created_at': _iso(getattr(r, 'created_at', None)),
        'archived_at': _iso(getattr(r, 'archived_at', None)),
    }


def _chat_row(m) -> dict:
    return {
        'message_id': getattr(m, 'message_id', None),
        'kind': getattr(m, 'kind', None),
        'body': getattr(m, 'body', None),
        'author_id': getattr(m, 'author_id', None),
        'created_at': _iso(getattr(m, 'created_at', None)),
    }


def _sitrep(s) -> dict:
    return {
        'sitrep_id': getattr(s, 'sitrep_id', None),
        'version': getattr(s, 'version', None),
        'title': getattr(s, 'title', None),
        'body_md': getattr(s, 'body_md', None),
        'authored_by_id': getattr(s, 'authored_by_id', None),
        'authored_at': _iso(getattr(s, 'authored_at', None)),
        'published': bool(getattr(s, 'published', False)),
    }


def _note(n) -> dict:
    return {
        'note_id': getattr(n, 'note_id', None),
        'title': getattr(n, 'title', None),
        'content': getattr(n, 'content', None),
        'created_by_id': getattr(n, 'created_by_id', None),
        'folder_id': getattr(n, 'folder_id', None),
        'created_at': _iso(getattr(n, 'created_at', None)),
    }


def _task(t) -> dict:
    return {
        'task_id': getattr(t, 'task_id', None),
        'title': getattr(t, 'title', None),
        'description': getattr(t, 'description', None),
        'status_id': getattr(t, 'status_id', None),
        'assignee_id': getattr(t, 'assignee_id', None),
        'created_by_id': getattr(t, 'created_by_id', None),
        'created_at': _iso(getattr(t, 'created_at', None)),
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None
