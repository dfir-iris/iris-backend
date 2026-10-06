#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS

"""MCP tools for the War Rooms domain.

Yuki (the chatbot) uses these to read and mutate war-rooms when the
conversation is scoped to one. `war_room_scoped=True` on the decorator
adds a `war_room_id` slot to each tool's input schema, and dispatch
enforces access before invocation — the loop also overrides the id
with the conversation's scope on every call (see loop.py §7 for the
prompt-injection defence rationale).

Authorization mirrors `war_rooms/access.py`: reads declare
`war_rooms_read` and need `read_only` on the room, writes declare
`war_rooms_write` and need `full_access` (dispatch derives the level
from `mcp/classification.py`). Server admins pass either way, as they
do on the REST routes.

Read, creation and note-edit tools are exposed. Delete / archive is
deliberately excluded — the analyst can perform those from the UI,
and letting an LLM propose them would be a common footgun.
"""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.war_rooms.access import war_room_readable_attached_case_ids
from app.iris_engine.collab.sync import collab_current_markdown
from app.iris_engine.collab.sync import collab_replace_markdown
from app.business import (
    war_room_chat as war_room_chat_biz,
    war_room_decisions as war_room_decisions_biz,
    war_room_notes as war_room_notes_biz,
    war_room_sitreps as war_room_sitreps_biz,
    war_room_tasks as war_room_tasks_biz,
    war_rooms as war_rooms_biz,
)
from app.models.authorization import CaseAccessLevel
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_chat_list(args: dict) -> dict:
    try:
        rows = war_room_chat_biz.list_messages(
            war_room_id=args['war_room_id'],
            limit=args.get('limit') or 50,
            search=args.get('search'),
            readable_case_ids=war_room_readable_attached_case_ids(args['war_room_id']),
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
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
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
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_list(args: dict) -> dict:
    rows = war_room_notes_biz.war_room_note_list(args['war_room_id'])
    # Live content: the column lags while someone has the note open,
    # and an edit built on it would revert their unsaved typing.
    return {'notes': [_note(n, live=True) for n in rows]}


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
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
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


@mcp_tool(
    name='iris_war_room_notes_update',
    description=(
        'Edit an existing war-room note: rename it and/or replace its '
        'body. `content` is the FULL new markdown body, not a diff — '
        'start from the content returned by iris_war_room_notes_list '
        'and keep every part that should stay. Omit a field to leave '
        'it unchanged. The previous version is kept in the note history.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'note_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'content': {
                'type': 'string',
                'description': 'Full new markdown body.',
            },
        },
        'required': ['note_id'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_notes_update(args: dict) -> dict:
    if args.get('title') is None and args.get('content') is None:
        raise MCPError(protocol.INVALID_PARAMS,
                       'Nothing to update: provide a title and/or content.')
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    try:
        row = war_room_notes_biz.war_room_note_update(
            war_room_id=args['war_room_id'],
            note_id=args['note_id'],
            title=args.get('title'),
            content=args.get('content'),
            updated_by_id=user_obj.id,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Note #{args["note_id"]} not found in this war room.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    if args.get('content') is not None:
        # Once the note has been opened the Y.Doc is authoritative:
        # without this the editor keeps showing the old body and the
        # next flush writes it back over the column.
        collab_replace_markdown(f'war-room-note:{row.note_id}', args['content'])
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
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_tasks_list(args: dict) -> dict:
    rows = war_room_tasks_biz.war_room_task_list(
        war_room_id=args['war_room_id'],
        q=args.get('q'),
    )
    teams = war_room_tasks_biz.war_room_task_teams([t.task_id for t in rows])
    return {'tasks': [_task(t, teams.get(t.task_id)) for t in rows]}


@mcp_tool(
    name='iris_war_room_tasks_create',
    description='Create a task in a war room.',
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1},
            'description': {'type': 'string'},
            'team_ids': {
                'type': 'array', 'items': {'type': 'integer'},
                'description': 'Teams of this war room assigned to the task.',
            },
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
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
            team_ids=args.get('team_ids'),
        )
    except (BusinessProcessingError, ValidationError) as exc:
        msg = exc.get_message() if isinstance(exc, BusinessProcessingError) else str(exc)
        raise MCPError(protocol.INVALID_PARAMS, msg) from exc
    return _task(row, war_room_tasks_biz.war_room_task_teams([row.task_id]).get(row.task_id))


# ---- Decisions -----------------------------------------------------

_DECISION_CREATE_FIELDS = ('title', 'rationale', 'status', 'target_at', 'owner_id',
                           'approver_ids', 'case_ids')


def _decision_readable_case_ids(case_ids) -> set:
    if not case_ids:
        return set()
    return set(ac_fast_check_current_user_has_cases_access(
        list(case_ids), [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    ))


def _decisions_payload(decisions) -> list:
    readable = _decision_readable_case_ids(
        war_room_decisions_biz.war_room_decisions_referenced_case_ids(decisions))
    return war_room_decisions_biz.war_room_decisions_serialize(decisions, readable)


@mcp_tool(
    name='iris_war_room_decisions_list',
    description=(
        'List the decision register (D-n) of a war room, newest first. '
        'Each decision carries its status, target date, overdue flag, '
        'approvers and linked cases / assets.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'status': {
                'type': 'array',
                'items': {
                    'type': 'string',
                    'enum': ['proposed', 'approved', 'rejected', 'superseded'],
                },
                'description': 'Only return decisions in these statuses.',
            },
            'q': {'type': 'string', 'description': 'Free-text match on the title.'},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_decisions_list(args: dict) -> dict:
    status = args.get('status')
    if isinstance(status, str):
        status = [status]
    q = args.get('q')
    try:
        rows = war_room_decisions_biz.war_room_decisions_list(
            args['war_room_id'],
            status=status if isinstance(status, list) else None,
            q=q if isinstance(q, str) else None,
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    return {'decisions': _decisions_payload(rows)}


@mcp_tool(
    name='iris_war_room_decisions_create',
    description=(
        'Register a decision in the war room decision register. Status '
        'defaults to proposed; target_at is an ISO-8601 date-time (UTC '
        'if no offset). case_ids must be cases attached to the room; '
        'approver_ids / owner_id must be users with access to the room.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'rationale': {'type': 'string'},
            'status': {'type': 'string', 'enum': ['proposed', 'approved']},
            'target_at': {'type': 'string', 'description': 'ISO-8601 date-time.'},
            'owner_id': {'type': 'integer'},
            'approver_ids': {'type': 'array', 'items': {'type': 'integer'}},
            'case_ids': {'type': 'array', 'items': {'type': 'integer'}},
        },
        'required': ['title'],
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_decisions_create(args: dict) -> dict:
    user_obj = iris_current_user._get_current_object()  # type: ignore[attr-defined]
    war_room_id = args['war_room_id']
    raw = {k: args[k] for k in _DECISION_CREATE_FIELDS if k in args}
    try:
        # Linking a case requires read access on it; an unreadable case
        # gets the same answer as an unattached one.
        case_ids = war_room_decisions_biz.war_room_decisions_parse_case_ids(raw.get('case_ids'))
        readable = _decision_readable_case_ids(case_ids)
        for case_id in case_ids:
            if case_id not in readable:
                raise BusinessProcessingError(f'Case #{case_id} is not attached to this war room')
        decision = war_room_decisions_biz.war_room_decisions_create(
            war_room_id, raw, created_by_id=user_obj.id)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc
    war_room_chat_biz.emit_system_event(
        war_room_id, 'decision', f'D-{decision.number} {decision.title}',
        author_id=user_obj.id,
        ref_type='war_room_decision', ref_id=decision.decision_id,
    )
    return _decisions_payload([decision])[0]


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


def _note(n, live: bool = False) -> dict:
    content = getattr(n, 'content', None)
    if live:
        content = collab_current_markdown(f'war-room-note:{n.note_id}', content)
    return {
        'note_id': getattr(n, 'note_id', None),
        'title': getattr(n, 'title', None),
        'content': content,
        'created_by_id': getattr(n, 'created_by_id', None),
        'folder_id': getattr(n, 'folder_id', None),
        'created_at': _iso(getattr(n, 'created_at', None)),
    }


def _task(t, teams=None) -> dict:
    return {
        'task_id': getattr(t, 'task_id', None),
        'title': getattr(t, 'title', None),
        'description': getattr(t, 'description', None),
        'status_id': getattr(t, 'status_id', None),
        'assignee_id': getattr(t, 'assignee_id', None),
        'teams': teams or [],
        'created_by_id': getattr(t, 'created_by_id', None),
        'created_at': _iso(getattr(t, 'created_at', None)),
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None
