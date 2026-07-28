#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP resource templates — read-only URI-addressable views.

Small set on purpose: Claude Desktop today only reads resources on user
click, not autonomously, so a wide resource surface is low-ROI. Each
resource is a synthetic REST GET routed through `iris_current_user`, so
per-user access control is identical to REST semantics.
"""
from __future__ import annotations

from app.blueprints.access_controls import (
    ac_current_user_has_customer_access,
    ac_current_user_permissions_mask,
    ac_fast_check_current_user_has_case_access,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_resource
from app.business.alerts import alerts_get
from app.business.assets import assets_filter
from app.business.cases import cases_get_by_identifier
from app.business.iocs import iocs_filter
from app.business.notes import notes_search
from app.business.server_settings import get_srv_settings
from app.business.tasks import tasks_filter
from app.business.users import users_get
from app.models.authorization import CaseAccessLevel, Permissions
from app.models.errors import ObjectNotFoundError
from app.models.pagination_parameters import PaginationParameters
from app.schema.marshables import (
    AlertSchema,
    CaseAssetsSchema,
    CaseNoteSchema,
    CaseSchemaForAPIV2,
    CaseTaskSchema,
    IocSchemaForAPIV2,
    UserSchemaForAPIV2,
)


def _require_case_access(case_id: int) -> None:
    if not ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        raise MCPError(protocol.IRIS_ACCESS_DENIED,
                       f'No access to case #{case_id}.')


@mcp_resource(
    uri_template='iris://cases/{case_id}',
    name='iris_case',
    description='Full IRIS case record.',
    permissions=(Permissions.standard_user,),
)
def _resource_case(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    try:
        case = cases_get_by_identifier(case_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    return CaseSchemaForAPIV2().dump(case)


@mcp_resource(
    uri_template='iris://cases/{case_id}/iocs',
    name='iris_case_iocs',
    description='IOCs attached to a case.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_iocs(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    result = iocs_filter(case_id, PaginationParameters(1, 100, None, 'asc'), {})
    return {'data': IocSchemaForAPIV2().dump(result.items, many=True),
            'total': result.total}


@mcp_resource(
    uri_template='iris://cases/{case_id}/assets',
    name='iris_case_assets',
    description='Assets attached to a case.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_assets(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    result = assets_filter(case_id, PaginationParameters(1, 100, None, 'asc'), {})
    return {'data': CaseAssetsSchema().dump(result.items, many=True),
            'total': result.total}


@mcp_resource(
    uri_template='iris://cases/{case_id}/notes',
    name='iris_case_notes',
    description='Notes attached to a case (title + summary).',
    permissions=(Permissions.standard_user,),
)
def _resource_case_notes(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    # `%` is the SQL wildcard used by notes_search when the caller wants
    # every note in a case — the REST GET normally passes user-typed text
    # here, but for the resource browse we want the full set.
    notes = notes_search(case_id, '%')
    return {'notes': CaseNoteSchema().dump(notes, many=True)}


@mcp_resource(
    uri_template='iris://cases/{case_id}/tasks',
    name='iris_case_tasks',
    description='Tasks attached to a case.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_tasks(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    result = tasks_filter(case_id, PaginationParameters(1, 100, None, 'asc'))
    return {'data': CaseTaskSchema().dump(result.items, many=True),
            'total': result.total}


@mcp_resource(
    uri_template='iris://alerts/{alert_id}',
    name='iris_alert',
    description='Full IRIS alert record.',
    permissions=(Permissions.alerts_read,),
)
def _resource_alert(params: dict) -> dict:
    alert_id = int(params['alert_id'])
    try:
        alert = alerts_get(
            iris_current_user,
            ac_current_user_permissions_mask(),
            alert_id,
            fallback_customer_access=ac_current_user_has_customer_access,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       'Alert not found or access denied.') from exc
    return AlertSchema().dump(alert)


@mcp_resource(
    uri_template='iris://me',
    name='iris_me',
    description='Calling user profile.',
    permissions=(Permissions.standard_user,),
)
def _resource_me(_params: dict) -> dict:
    user = users_get(iris_current_user.id)
    return UserSchemaForAPIV2().dump(user)


@mcp_resource(
    uri_template='iris://me/context',
    name='iris_me_context',
    description='Calling user runtime context (permissions, IRIS version).',
    permissions=(Permissions.standard_user,),
)
def _resource_me_context(_params: dict) -> dict:
    # Import lazily to avoid a circular import at package init time.
    from app.blueprints.rest.v2.mcp.tools.profile import iris_me_context_get
    return iris_me_context_get({})


@mcp_resource(
    uri_template='iris://runtime-config',
    name='iris_runtime_config',
    description='Server runtime configuration surface (mirrors GET /api/v2/runtime-config).',
    permissions=(Permissions.standard_user,),
)
def _resource_runtime_config(_params: dict) -> dict:
    settings = get_srv_settings()
    return {
        'mcp': {
            'enabled': bool(settings.mcp_enabled),
            'endpoint': '/api/v2/mcp',
        },
    }
