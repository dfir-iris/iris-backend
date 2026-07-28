#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped Notes domain."""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.business.notes import (
    notes_create,
    notes_get,
    notes_search,
    notes_update,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseNoteSchema


_note_schema = CaseNoteSchema()


def _get_note_in_case(note_id: int, case_id: int):
    note = notes_get(note_id)
    if note.note_case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Note #{note_id} does not belong to case #{case_id}.')
    return note


@mcp_tool(
    name='iris_case_notes_list',
    description=(
        'Search case notes by free-text; returns matching note summaries. '
        'Pass an empty string to list all notes in the case.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'search_input': {'type': 'string'},
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_list(args: dict) -> dict:
    search_input = (args.get('search_input') or '').strip() or '%'
    try:
        notes = notes_search(args['case_identifier'], search_input)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'notes': _note_schema.dump(notes, many=True)}


@mcp_tool(
    name='iris_case_notes_get',
    description='Fetch a single note by ID.',
    input_schema={
        'type': 'object',
        'properties': {'note_identifier': {'type': 'integer'}},
        'required': ['note_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_get(args: dict) -> dict:
    try:
        note = _get_note_in_case(args['note_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Note not found.') from exc
    return _note_schema.dump(note)


@mcp_tool(
    name='iris_case_notes_create',
    description='Create a note in a case.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Note fields — see CaseNoteSchema (note_title, note_content, ...).',
            },
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_create(args: dict) -> dict:
    try:
        payload = dict(args['payload'])
        _note_schema.verify_directory_id(payload, caseid=args['case_identifier'])
        note = _note_schema.load(payload)
        note = notes_create(note, args['case_identifier'])
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _note_schema.dump(note)


@mcp_tool(
    name='iris_case_notes_update',
    description='Update a note (partial payload accepted). Automatically versions.',
    input_schema={
        'type': 'object',
        'properties': {
            'note_identifier': {'type': 'integer'},
            'payload': {'type': 'object'},
        },
        'required': ['note_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_update(args: dict) -> dict:
    try:
        note = _get_note_in_case(args['note_identifier'], args['case_identifier'])
        payload = dict(args['payload'])
        payload['note_id'] = note.note_id
        _note_schema.load(payload, partial=True, instance=note)
        note = notes_update(iris_current_user, note)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Note not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Validation error: {exc.normalized_messages()}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _note_schema.dump(note)
