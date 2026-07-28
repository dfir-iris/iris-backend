#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the case-scoped Assets domain."""
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
from app.business.assets import (
    assets_create,
    assets_delete,
    assets_filter,
    assets_get,
    assets_update,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import CaseAssetsSchema


_asset_schema = CaseAssetsSchema()


def _get_asset_in_case(asset_id: int, case_id: int):
    asset = assets_get(asset_id)
    if asset.case_id != case_id:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Asset #{asset_id} does not belong to case #{case_id}.')
    return asset


@mcp_tool(
    name='iris_case_assets_list',
    description='List assets attached to a case.',
    input_schema={
        'type': 'object',
        'properties': PAGINATION_SCHEMA_FRAGMENT,
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_list(args: dict) -> dict:
    try:
        result = assets_filter(args['case_identifier'], build_pagination(args), {})
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return dump_paginated(_asset_schema, result)


@mcp_tool(
    name='iris_case_assets_get',
    description='Fetch a single asset by ID.',
    input_schema={
        'type': 'object',
        'properties': {'asset_identifier': {'type': 'integer'}},
        'required': ['asset_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_get(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_create',
    description='Add a new asset to a case.',
    input_schema={
        'type': 'object',
        'properties': {
            'payload': {'type': 'object'},
            'ioc_links': {'type': 'array'},
        },
        'required': ['payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_create(args: dict) -> dict:
    try:
        asset = _asset_schema.load(args['payload'])
        asset = assets_create(
            iris_current_user,
            args['case_identifier'],
            asset,
            args.get('ioc_links') or [],
        )
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_update',
    description='Update an existing case asset (partial payload accepted).',
    input_schema={
        'type': 'object',
        'properties': {
            'asset_identifier': {'type': 'integer'},
            'payload': {'type': 'object'},
        },
        'required': ['asset_identifier', 'payload'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_update(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
        _asset_schema.load(args['payload'], instance=asset, partial=True)
        asset = assets_update(asset)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _asset_schema.dump(asset)


@mcp_tool(
    name='iris_case_assets_delete',
    description='Delete an asset from a case.',
    input_schema={
        'type': 'object',
        'properties': {'asset_identifier': {'type': 'integer'}},
        'required': ['asset_identifier'],
    },
    permissions=(Permissions.standard_user,),
    case_scoped=True,
    mvp=True,
)
def iris_case_assets_delete(args: dict) -> dict:
    try:
        asset = _get_asset_in_case(args['asset_identifier'], args['case_identifier'])
        assets_delete(asset)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Asset not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'deleted': True, 'asset_id': args['asset_identifier']}
