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
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    build_pagination,
    dump_paginated,
)
from app.business.notes import (
    notes_create,
    notes_get,
    notes_search,
    notes_update,
)
from app.business.notes_directories import (
    notes_directories_create,
    notes_directories_filter,
    notes_directories_get_or_create_root,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseNoteDirectorySchema, CaseNoteSchema


_note_schema = CaseNoteSchema()
_directory_schema = CaseNoteDirectorySchema()


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
    description=(
        'Create a note in a case. `directory_id` is optional — if omitted, '
        'the note is placed in the case top-level directory automatically '
        '(one is created when the case has no directory yet).'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Note fields.',
                'properties': {
                    'note_title': {
                        'type': 'string',
                        'maxLength': 155,
                        'description': 'Note title (short).',
                    },
                    'note_content': {
                        'type': 'string',
                        'description': 'Note body, markdown accepted.',
                    },
                    'directory_id': {
                        'type': 'integer',
                        'description': (
                            'Target directory id. Omit to place the note in '
                            'the case top-level directory (auto-resolved).'
                        ),
                    },
                    'custom_attributes': {
                        'type': 'object',
                        'description': 'Optional per-note custom attributes.',
                    },
                },
                'required': ['note_title', 'note_content'],
                'additionalProperties': False,
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
        case_id = args['case_identifier']
        # If the model didn't pick a directory (very common — the LLM
        # doesn't know the directory tree unless it also listed it), fall
        # back to the case's top-level directory. `verify_directory_id` in
        # the schema demands a non-null directory_id, so we resolve one
        # here — creating it when the case has an empty tree, which is the
        # default for any case not built from a template.
        if not payload.get('directory_id'):
            payload['directory_id'] = notes_directories_get_or_create_root(case_id).id
        _note_schema.verify_directory_id(payload, caseid=case_id)
        note = _note_schema.load(payload)
        note = notes_create(note, case_id)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _note_schema.dump(note)


@mcp_tool(
    name='iris_case_notes_directories_list',
    description=(
        'List the note directories (folders) of a case. Use it to resolve a '
        'folder name to the `directory_id` expected by iris_case_notes_create.'
    ),
    input_schema={
        'type': 'object',
        'properties': PAGINATION_SCHEMA_FRAGMENT,
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_directories_list(args: dict) -> dict:
    result = notes_directories_filter(args['case_identifier'], build_pagination(args))
    return dump_paginated(_directory_schema, result)


@mcp_tool(
    name='iris_case_notes_directories_create',
    description=(
        'Create a note directory (folder) in a case. Omit `parent_id` for a '
        'top-level directory, or pass the id of an existing directory of the '
        'same case to nest the new one under it.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'Directory name.'},
            'parent_id': {
                'type': 'integer',
                'description': (
                    'Parent directory id. Omit to create a top-level directory.'
                ),
            },
        },
        'required': ['name'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_notes_directories_create(args: dict) -> dict:
    case_id = args['case_identifier']
    payload = {'name': args['name'], 'case_id': case_id}
    try:
        if args.get('parent_id') is not None:
            # Rejects a parent that belongs to another case, so a stray id
            # can't graft a directory onto someone else's tree.
            _directory_schema.verify_parent_id(args['parent_id'], case_id=case_id)
            payload['parent_id'] = args['parent_id']
        directory = _directory_schema.load(payload)
        notes_directories_create(directory)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Validation error: {exc.normalized_messages()}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _directory_schema.dump(directory)


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
