#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the Cases domain."""
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
from app.business.cases import (
    cases_close,
    cases_create,
    cases_filter,
    cases_get_by_identifier,
    cases_reopen,
    cases_update,
)
from app.db import db
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseDetailsSchema, CaseSchemaForAPIV2


_case_schema = CaseSchemaForAPIV2()
_case_details_schema = CaseDetailsSchema()


@mcp_tool(
    name='iris_cases_list',
    description='List IRIS cases visible to the caller, with optional quick-search.',
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            'quick_search': {
                'type': 'string',
                'description': 'Free-text match on case name, customer name, or numeric ID.',
            },
            'is_open': {
                'type': 'boolean',
                'description': 'True → only open cases, False → only closed, omit → both.',
            },
        },
    },
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_cases_list(args: dict) -> dict:
    result = cases_filter(
        iris_current_user,
        build_pagination(args),
        quick_search=args.get('quick_search'),
        is_open=args.get('is_open'),
    )
    if result is None:
        raise MCPError(protocol.INTERNAL_ERROR, 'Case filter returned no result.')
    return dump_paginated(_case_details_schema, result)


@mcp_tool(
    name='iris_cases_filter',
    description=(
        'Filter cases by structured fields (customer, classification, owner, '
        'severity, state, SOC id, dates). Returns paginated case summaries.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            'case_name': {'type': 'string'},
            'case_description': {'type': 'string'},
            'case_customer_id': {'type': 'string'},
            'case_classification_id': {'type': 'integer'},
            'case_owner_id': {'type': 'integer'},
            'case_opening_user_id': {'type': 'integer'},
            'case_severity_id': {'type': 'integer'},
            'case_state_id': {'type': 'integer'},
            'case_soc_id': {'type': 'string'},
            'is_open': {'type': 'boolean'},
            'start_open_date': {'type': 'string', 'description': 'ISO date'},
            'end_open_date': {'type': 'string', 'description': 'ISO date'},
        },
    },
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_cases_filter(args: dict) -> dict:
    result = cases_filter(
        iris_current_user,
        build_pagination(args),
        name=args.get('case_name'),
        customer_identifier=args.get('case_customer_id'),
        description=args.get('case_description'),
        classification_identifier=args.get('case_classification_id'),
        owner_identifier=args.get('case_owner_id'),
        opening_user_identifier=args.get('case_opening_user_id'),
        severity_identifier=args.get('case_severity_id'),
        status_identifier=args.get('case_state_id'),
        soc_identifier=args.get('case_soc_id'),
        start_open_date=args.get('start_open_date'),
        end_open_date=args.get('end_open_date'),
        is_open=args.get('is_open'),
    )
    if result is None:
        raise MCPError(protocol.INTERNAL_ERROR, 'Case filter returned no result.')
    return dump_paginated(_case_details_schema, result)


@mcp_tool(
    name='iris_cases_get',
    description='Fetch a single case by ID.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_cases_get(args: dict) -> dict:
    try:
        case = cases_get_by_identifier(args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    return _case_schema.dump(case)


@mcp_tool(
    name='iris_cases_create',
    description='Create a new case. Accepts the same payload shape as POST /api/v2/cases.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {
                'type': 'object',
                'description': 'Case fields — see CaseSchemaForAPIV2.',
            },
            'case_template_id': {
                'type': 'integer',
                'description': 'Optional case template to seed the case with.',
            },
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    mvp=True,
)
def iris_cases_create(args: dict) -> dict:
    payload = dict(args['payload'])
    if 'case_template_id' in args:
        payload['case_template_id'] = args['case_template_id']
    try:
        case = _case_schema.load(payload, session=db.session)
        template_id = payload.pop('case_template_id', None)
        case = cases_create(iris_current_user, case, template_id)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _case_schema.dump(case)


@mcp_tool(
    name='iris_cases_update',
    description='Update a case. Payload accepts partial fields.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {'type': 'object'},
            'protagonists': {'type': 'array'},
            'tags': {'type': 'string'},
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_cases_update(args: dict) -> dict:
    try:
        case = cases_get_by_identifier(args['case_identifier'])
        updated = _case_schema.load(
            args['payload'], instance=case, partial=True, session=db.session
        )
        case = cases_update(
            case, updated,
            args.get('protagonists') or [],
            args.get('tags') or '',
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _case_schema.dump(case)


@mcp_tool(
    name='iris_cases_close',
    description='Close a case.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_cases_close(args: dict) -> dict:
    try:
        case = cases_close(args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    return _case_schema.dump(case)


@mcp_tool(
    name='iris_cases_reopen',
    description='Reopen a previously-closed case.',
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_cases_reopen(args: dict) -> dict:
    try:
        case = cases_reopen(args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    return _case_schema.dump(case)
