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
from app.business.access_controls import check_ua_case_client
from app.business.alerts import (
    alerts_escalate,
    alerts_get,
    alerts_get_related,
    alerts_merge,
    alerts_search,
    alerts_update,
)
from app.models.alerts import AlertResolutionStatus, AlertStatus, Severity
from app.models.authorization import Permissions
from app.models.cases import CaseClassification
from app.models.errors import BusinessProcessingError, ObjectNotFoundError
from app.schema.marshables import AlertSchema
from marshmallow.exceptions import ValidationError

import copy


_alert_schema = AlertSchema()


# Fields the LLM is allowed to update via iris_alerts_update. Kept in
# lock-step with the REST endpoint's _ALERT_READONLY_UPDATE_FIELDS
# (alert_id / alert_customer_id / alert_creation_time are immutable —
# see GHSA-8hwq-v6vm-9grr / SBA-ADV-20260128-05 / CWE-863).
_ALERT_UPDATE_PROPERTIES: dict[str, dict] = {
    'alert_title':                 {'type': 'string'},
    'alert_description':           {'type': 'string'},
    'alert_source':                {'type': 'string'},
    'alert_source_ref':            {'type': 'string'},
    'alert_source_link':           {'type': 'string'},
    'alert_source_event_time':     {'type': 'string', 'description': 'ISO-8601 datetime.'},
    'alert_note':                  {'type': 'string', 'description': 'Free-form analyst note.'},
    'alert_tags':                  {'type': 'string', 'description': 'Comma-separated tags.'},
    'alert_severity_id':           {'type': 'integer'},
    'alert_status_id':             {'type': 'integer'},
    'alert_resolution_status_id':  {'type': ['integer', 'null']},
    'alert_classification_id':     {'type': ['integer', 'null']},
    'alert_owner_id':              {
        'type': ['integer', 'null'],
        'description': 'User id to assign as owner. Use -1 or null to unassign.',
    },
    'alert_context':               {'type': 'object'},
}


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
    target_case_id = args['target_case_id']
    # Same entitlement REST's merge_alert enforces: being allowed to read
    # the alert says nothing about the *target* case, so without this an
    # alert could be walked into a case belonging to another customer.
    if not check_ua_case_client(iris_current_user.id, target_case_id):
        raise MCPError(
            protocol.IRIS_ACCESS_DENIED,
            f'Not entitled to merge alerts into case #{target_case_id}.',
        )
    try:
        case = alerts_merge(
            alert,
            target_case_id,
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


@mcp_tool(
    name='iris_alerts_update',
    description=(
        'Update an alert. Payload accepts partial fields — omit fields you do not want to '
        'change. Common uses: assign an owner (`alert_owner_id`), set the workflow status '
        '(`alert_status_id`), record a resolution (`alert_resolution_status_id`), change '
        'severity (`alert_severity_id`), classify (`alert_classification_id`), add tags '
        '(`alert_tags`), append an analyst note (`alert_note`). Use the corresponding '
        '`iris_alerts_*_list` tools to discover the numeric ids first. Pass '
        '`alert_owner_id: -1` (or null) to unassign an owner.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'alert_identifier': {'type': 'integer'},
            'payload': {
                'type': 'object',
                'description': 'Partial alert fields to update.',
                'properties': _ALERT_UPDATE_PROPERTIES,
                'additionalProperties': False,
            },
        },
        'required': ['alert_identifier', 'payload'],
    },
    permissions=(Permissions.alerts_write,),
    mvp=True,
)
def iris_alerts_update(args: dict) -> dict:
    alert = _get_alert(args['alert_identifier'])
    payload = dict(args.get('payload') or {})
    if not payload:
        raise MCPError(protocol.INVALID_PARAMS, 'Payload must contain at least one field.')

    pristine_alert = copy.copy(alert)

    # Mirror the REST route's "unassign owner" convention so the LLM
    # sees a single consistent semantics for clearing an assignee.
    if payload.get('alert_owner_id') in (-1, '-1'):
        payload['alert_owner_id'] = None

    try:
        updated_alert = _alert_schema.load(payload, instance=alert, partial=True)
    except ValidationError as exc:
        raise MCPError(protocol.INVALID_PARAMS, f'Validation error: {exc.messages}') from exc

    activity_data = []
    for key, value in payload.items():
        old_value = getattr(pristine_alert, key, None)
        if key not in ('alert_content', 'alert_note'):
            activity_data.append(f'"{key}" from "{old_value}" to "{value}"')
        else:
            activity_data.append(f'"{key}"')

    try:
        result = alerts_update(pristine_alert, updated_alert, activity_data)
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INTERNAL_ERROR, exc.get_message()) from exc
    return _alert_schema.dump(result)


@mcp_tool(
    name='iris_alerts_status_list',
    description=(
        'List the alert workflow statuses configured on this instance '
        '(e.g. New, Assigned, In progress, Closed). Returns the numeric '
        '`status_id` values usable in `iris_alerts_update.alert_status_id` '
        'and in `iris_alerts_list` filters.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_status_list(args: dict) -> dict:
    rows = AlertStatus.query.order_by(AlertStatus.status_id).all()
    return {
        'data': [
            {
                'status_id': r.status_id,
                'status_name': r.status_name,
                'status_description': r.status_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_resolution_list',
    description=(
        'List the alert resolution statuses (e.g. True positive, False '
        'positive, Benign, Not applicable). Returns numeric '
        '`resolution_status_id` values usable in '
        '`iris_alerts_update.alert_resolution_status_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_resolution_list(args: dict) -> dict:
    rows = AlertResolutionStatus.query.order_by(AlertResolutionStatus.resolution_status_id).all()
    return {
        'data': [
            {
                'resolution_status_id': r.resolution_status_id,
                'resolution_status_name': r.resolution_status_name,
                'resolution_status_description': r.resolution_status_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_severity_list',
    description=(
        'List the severity levels configured on this instance (e.g. Low, '
        'Medium, High, Critical). Returns numeric `severity_id` values '
        'usable in `iris_alerts_update.alert_severity_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_severity_list(args: dict) -> dict:
    rows = Severity.query.order_by(Severity.severity_id).all()
    return {
        'data': [
            {
                'severity_id': r.severity_id,
                'severity_name': r.severity_name,
                'severity_description': r.severity_description,
            }
            for r in rows
        ]
    }


@mcp_tool(
    name='iris_alerts_classification_list',
    description=(
        'List the alert / case classifications configured on this '
        'instance (MITRE-style categories). Returns numeric `id` values '
        'usable in `iris_alerts_update.alert_classification_id`.'
    ),
    input_schema={'type': 'object', 'properties': {}},
    permissions=(Permissions.alerts_read,),
    mvp=True,
)
def iris_alerts_classification_list(args: dict) -> dict:
    rows = CaseClassification.query.order_by(CaseClassification.id).all()
    return {
        'data': [
            {
                'id': r.id,
                'name': r.name,
                'name_expanded': r.name_expanded,
                'description': r.description,
            }
            for r in rows
        ]
    }
