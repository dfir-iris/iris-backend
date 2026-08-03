#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped Tasks domain."""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    build_pagination,
    dump_paginated,
)
from app.business.tasks import (
    tasks_create,
    tasks_filter,
    tasks_get,
    tasks_update,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseTaskSchema


_task_schema = CaseTaskSchema()


def _get_task_in_case(task_id: int, case_id: int):
    task = tasks_get(task_id)
    if task.task_case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Task #{task_id} does not belong to case #{case_id}.')
    return task


@mcp_tool(
    name='iris_case_tasks_list',
    description='List tasks attached to a case.',
    input_schema={
        'type': 'object',
        'properties': PAGINATION_SCHEMA_FRAGMENT,
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_tasks_list(args: dict) -> dict:
    result = tasks_filter(args['case_identifier'], build_pagination(args))
    return dump_paginated(_task_schema, result)


@mcp_tool(
    name='iris_case_tasks_get',
    description='Fetch a single task by ID.',
    input_schema={
        'type': 'object',
        'properties': {'task_identifier': {'type': 'integer'}},
        'required': ['task_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_tasks_get(args: dict) -> dict:
    try:
        task = _get_task_in_case(args['task_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Task not found.') from exc
    return _task_schema.dump(task)


@mcp_tool(
    name='iris_case_tasks_create',
    description='Create a task in a case.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Task fields — see CaseTaskSchema.',
            },
            'task_assignees_id': {'type': 'array'},
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_tasks_create(args: dict) -> dict:
    try:
        payload = dict(args['payload'])
        payload['task_case_id'] = args['case_identifier']
        task = _task_schema.load(payload)
        task = tasks_create(task, args.get('task_assignees_id') or [])
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _task_schema.dump(task)


@mcp_tool(
    name='iris_case_tasks_update',
    description='Update a task (partial payload accepted).',
    input_schema={
        'type': 'object',
        'properties': {
            'task_identifier': {'type': 'integer'},
            'payload': {'type': 'object'},
            'task_assignees_id': {'type': 'array'},
        },
        'required': ['task_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_tasks_update(args: dict) -> dict:
    try:
        task = _get_task_in_case(args['task_identifier'], args['case_identifier'])
        _task_schema.load(args['payload'], instance=task, partial=True)
        task = tasks_update(task, args.get('task_assignees_id') or [])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Task not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _task_schema.dump(task)


@mcp_tool(
    name='iris_case_tasks_set_status',
    description=(
        'Set the status of a task by ID. Call `iris_taxonomies_list` first '
        'to resolve the target status name (e.g. "Done") to its numeric id '
        'via the `task_statuses` taxonomy.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'task_identifier': {'type': 'integer'},
            'task_status_id': {'type': 'integer'},
        },
        'required': ['task_identifier', 'task_status_id'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_tasks_set_status(args: dict) -> dict:
    try:
        task = _get_task_in_case(args['task_identifier'], args['case_identifier'])
        task.task_status_id = args['task_status_id']
        task = tasks_update(task, [a.user_id for a in getattr(task, 'assignees', [])])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Task not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _task_schema.dump(task)
