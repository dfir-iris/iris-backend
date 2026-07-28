#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP tools for the Alerts domain."""
from __future__ import annotations

from app.blueprints.access_controls import (
    ac_current_user_has_customer_access,
    ac_current_user_permissions_mask,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
)
from app.business.alerts import (
    alerts_escalate,
    alerts_get,
    alerts_get_related,
    alerts_merge,
    alerts_search,
)
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import AlertSchema


_alert_schema = AlertSchema()


def _get_alert(alert_id: int):
    try:
        return alerts_get(
            iris_current_user,
            ac_current_user_permissions_mask(),
            alert_id,
            fallback_customer_access=ac_current_user_has_customer_access,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       f'Alert #{alert_id} not found or access denied.') from exc


@mcp_tool(
    name='iris_alerts_list',
    description=(
        'Search alerts visible to the caller. All filters are optional; '
        'omit them to page through every alert the caller has access to.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'page': {'type': 'integer'},
            'per_page': {'type': 'integer'},
            'sort': {'type': 'string', 'description': 'Field to sort by, prefixed with - for descending.'},
            'title': {'type': 'string'},
            'description': {'type': 'string'},
            'status': {'type': 'string'},
            'severity': {'type': 'integer'},
            'owner': {'type': 'integer'},
            'source': {'type': 'string'},
            'tags': {'type': 'string'},
            'case_identifier': {'type': 'integer'},
            'customer_identifier': {'type': 'integer'},
            'classification': {'type': 'integer'},
            'alert_identifiers': {'type': 'string', 'description': 'Comma-separated list of IDs.'},
            'assets': {'type': 'string'},
            'iocs': {'type': 'string'},
            'resolution_status': {'type': 'integer'},
            'source_reference': {'type': 'string'},
            'start_date': {'type': 'string'},
            'end_date': {'type': 'string'},
            'source_start_date': {'type': 'string'},
            'source_end_date': {'type': 'string'},
        },
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_list(args: dict) -> dict:
    page = int(args.get('page') or 1)
    per_page = int(args.get('per_page') or DEFAULT_PER_PAGE)
    if per_page > MAX_PER_PAGE:
        per_page = MAX_PER_PAGE
    sort = args.get('sort') or 'desc'

    result = alerts_search(
        start_date=args.get('start_date'),
        end_date=args.get('end_date'),
        source_start_date=args.get('source_start_date'),
        source_end_date=args.get('source_end_date'),
        title=args.get('title'),
        description=args.get('description'),
        status=args.get('status'),
        severity=args.get('severity'),
        owner=args.get('owner'),
        source=args.get('source'),
        tags=args.get('tags'),
        case_identifier=args.get('case_identifier'),
        customer_identifier=args.get('customer_identifier'),
        classification=args.get('classification'),
        alert_identifiers=args.get('alert_identifiers'),
        assets=args.get('assets'),
        iocs=args.get('iocs'),
        resolution_status=args.get('resolution_status'),
        source_reference=args.get('source_reference'),
        custom_conditions=None,
        user_identifier_filter=iris_current_user.id,
        page=page,
        per_page=per_page,
        sort=sort,
    )
    return {
        'total': result.total,
        'data': _alert_schema.dump(result.items, many=True),
        'last_page': result.pages,
        'current_page': result.page,
        'next_page': result.next_num if result.has_next else None,
    }


@mcp_tool(
    name='iris_alerts_get',
    description='Fetch a single alert by ID.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_get(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    return _alert_schema.dump(alert)


@mcp_tool(
    name='iris_alerts_escalate',
    description=(
        'Escalate an alert to a brand-new case. Optionally imports IOCs '
        'and assets from the alert into the case.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'iocs_import_list': {'type': 'array'},
            'assets_import_list': {'type': 'array'},
            'note': {'type': 'string'},
            'import_as_event': {'type': 'boolean'},
            'case_tags': {'type': 'string'},
            'case_title': {'type': 'string'},
            'case_template_id': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_escalate(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    try:
        case = alerts_escalate(
            alert,
            iocs_import_list=args.get('iocs_import_list'),
            assets_import_list=args.get('assets_import_list'),
            note=args.get('note'),
            import_as_event=bool(args.get('import_as_event')),
            case_tags=args.get('case_tags'),
            case_title=args.get('case_title'),
            case_template_id=args.get('case_template_id'),
        )
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'case_id': case.case_id, 'case_name': case.name}


@mcp_tool(
    name='iris_alerts_merge',
    description='Merge an alert into an existing case.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'target_case_id': {'type': 'integer'},
            'iocs_import_list': {'type': 'array'},
            'assets_import_list': {'type': 'array'},
            'note': {'type': 'string'},
            'import_as_event': {'type': 'boolean'},
            'case_tags': {'type': 'string'},
        },
        'required': ['alert_identifier', 'target_case_id'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_merge(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    try:
        case = alerts_merge(
            alert,
            args['target_case_id'],
            iocs_import_list=args.get('iocs_import_list'),
            assets_import_list=args.get('assets_import_list'),
            note=args.get('note'),
            import_as_event=bool(args.get('import_as_event')),
            case_tags=args.get('case_tags'),
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Target case not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return {'case_id': case.case_id, 'case_name': case.name}


@mcp_tool(
    name='iris_alerts_related_get',
    description='Return the related-alerts graph payload for an alert.',
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'open_alerts': {'type': 'boolean'},
            'closed_alerts': {'type': 'boolean'},
            'open_cases': {'type': 'boolean'},
            'closed_cases': {'type': 'boolean'},
            'days_back': {'type': 'integer'},
            'number_of_nodes': {'type': 'integer'},
        },
        'required': ['alert_identifier'],
    },
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_related_get(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    days_back = int(args.get('days_back') or 180)
    if days_back < 0:
        days_back = 180
    number_of_results = int(args.get('number_of_nodes') or 100)
    if number_of_results < 0:
        number_of_results = 100
    return alerts_get_related(
        iris_current_user,
        alert,
        bool(args.get('open_alerts')),
        bool(args.get('closed_alerts')),
        bool(args.get('open_cases', True)),
        bool(args.get('closed_cases', True)),
        days_back,
        number_of_results,
    )
