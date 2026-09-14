#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped IOCs domain."""
from __future__ import annotations

from marshmallow import ValidationError

from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    PAGINATION_SCHEMA_FRAGMENT,
    VIEW_SCHEMA_FRAGMENT,
    build_pagination,
    dump_paginated,
    schema_for_view,
)
from app.business.iocs import (
    iocs_create,
    iocs_delete,
    iocs_filter,
    iocs_get,
    iocs_update,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import IocSchema, IocSchemaForAPIV2


_ioc_schema = IocSchemaForAPIV2()
_ioc_update_schema = IocSchema()

# Default projection for `iris_case_iocs_list`. Drops `ioc_enrichment`
# and `ioc_misp` — third-party enrichment documents whose size is set by
# whatever module wrote them — plus `modification_history`, `link` and
# `custom_attributes`.
_IOC_SUMMARY_FIELDS = (
    'ioc_id',
    'ioc_value',
    'ioc_description',
    'ioc_tags',
    'ioc_type_id',
    'ioc_tlp_id',
    'case_id',
    'ioc_type.type_name',
    'tlp.tlp_name',
)

_ioc_summary_schema = IocSchemaForAPIV2(only=_IOC_SUMMARY_FIELDS)


def _get_ioc_in_case(ioc_id: int, case_id: int):
    ioc = iocs_get(ioc_id)
    if ioc.case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'IOC #{ioc_id} does not belong to case #{case_id}.')
    return ioc


@mcp_tool(
    name='iris_case_iocs_list',
    description=(
        'List IOCs attached to a case. Rows come back as summaries; use '
        '`iris_case_iocs_get` for one IOC in full, including its enrichment.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            **VIEW_SCHEMA_FRAGMENT,
        },
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_iocs_list(args: dict) -> dict:
    result = iocs_filter(args['case_identifier'], build_pagination(args), {})
    return dump_paginated(
        schema_for_view(args, _ioc_summary_schema, _ioc_schema), result,
    )


@mcp_tool(
    name='iris_case_iocs_get',
    description='Fetch a single IOC by ID.',
    input_schema={
        'type': 'object',
        'properties': {'ioc_identifier': {'type': 'integer'}},
        'required': ['ioc_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_iocs_get(args: dict) -> dict:
    try:
        ioc = _get_ioc_in_case(args['ioc_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'IOC not found.') from exc
    return _ioc_schema.dump(ioc)


@mcp_tool(
    name='iris_case_iocs_create',
    description='Add a new IOC to a case.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {'type': 'object',
                        'description': 'IOC fields; see IocSchemaForAPIV2.'},
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_iocs_create(args: dict) -> dict:
    payload = dict(args['payload'])
    payload['case_id'] = args['case_identifier']
    try:
        ioc = _ioc_schema.load(payload)
        ioc = iocs_create(ioc)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _ioc_schema.dump(ioc)


@mcp_tool(
    name='iris_case_iocs_update',
    description=(
        'Update an existing case IOC (partial payload accepted). For '
        '`ioc_type_id` and `ioc_tlp_id`, call `iris_taxonomies_list` first '
        'to resolve names (e.g. "domain", "amber") to numeric ids.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'ioc_identifier': {'type': 'integer'},
            'payload': {'type': 'object'},
        },
        'required': ['ioc_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_iocs_update(args: dict) -> dict:
    try:
        ioc = _get_ioc_in_case(args['ioc_identifier'], args['case_identifier'])
        payload = dict(args['payload'])
        payload['ioc_id'] = ioc.ioc_id
        payload['case_id'] = ioc.case_id
        ioc_sc = _ioc_update_schema.load(payload, instance=ioc, partial=True)
        ioc = iocs_update(ioc, ioc_sc)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'IOC not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _ioc_schema.dump(ioc)


@mcp_tool(
    name='iris_case_iocs_delete',
    description='Delete an IOC from a case.',
    input_schema={
        'type': 'object',
        'properties': {'ioc_identifier': {'type': 'integer'}},
        'required': ['ioc_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_iocs_delete(args: dict) -> dict:
    try:
        ioc = _get_ioc_in_case(args['ioc_identifier'], args['case_identifier'])
        iocs_delete(ioc)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'IOC not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'deleted': True, 'ioc_id': args['ioc_identifier']}
