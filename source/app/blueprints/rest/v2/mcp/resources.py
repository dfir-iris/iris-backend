#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""MCP resource templates — read-only URI-addressable views.

Each resource is a synthetic REST GET routed through `iris_current_user`,
so per-user access control is identical to REST semantics. Resources are
grouped into three families:

  * `iris://cases/…`   — case-scoped views (ACL check via
    `ac_fast_check_current_user_has_case_access`).
  * `iris://alerts/…` / `iris://alert-clusters/…` — customer-scoped
    (permissions + customer-access mask).
  * `iris://war-rooms/…` — war-room-scoped ACL via
    `ac_fast_check_current_user_has_war_room_access`.
  * `iris://me`, `iris://runtime-config`, etc. — global / self-scoped.

Adding a new resource is one `@mcp_resource(...)` decorator + one
function that reads via `app.business.*`. The dispatch layer wraps the
call with permission enforcement based on the decorator's `permissions`
tuple; case/war-room scope is enforced inside the handler because the
`{case_id}` / `{war_room_id}` template variable determines the scope
target dynamically.
"""
from __future__ import annotations

from app.blueprints.access_controls import (
    ac_current_user_has_customer_access,
    ac_current_user_permissions_mask,
    ac_fast_check_current_user_has_case_access,
    ac_fast_check_current_user_has_war_room_access,
)
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_resource
from app.blueprints.rest.v2.war_rooms.serializers import (
    serialize_case_attachment,
    serialize_case_war_room_summary,
    serialize_member,
    serialize_war_room,
)
from app.business.activity import activity_search_in_case
from app.business.alert_clusters import (
    alert_cluster_correlation_graph,
    alert_clusters_get,
    alert_clusters_get_by_case,
)
from app.business.alerts import alerts_get
from app.business.assets import assets_filter
from app.business.case_timelines import case_timeline_list
from app.business.cases import cases_get_by_identifier
from app.business.evidences import evidences_filter
from app.business.events import events_list_filtered
from app.business.iocs import iocs_filter
from app.business.notes import notes_search
from app.business.server_settings import get_srv_settings
from app.business.tasks import tasks_filter
from app.business.users import users_get
from app.business.war_room_chat import list_messages, list_topics
from app.business.war_room_datastore import war_room_datastore_list
from app.business.war_room_linked_case_timelines import list_linked_case_timelines
from app.business.war_room_notes import war_room_note_list
from app.business.war_room_sitreps import sitrep_list
from app.business.war_room_tasks import war_room_task_list
from app.business.war_room_teams import war_room_team_list
from app.business.war_room_timelines import list_timeline_events, list_timelines
from app.business.war_rooms import (
    war_room_cases_list,
    war_room_get,
    war_room_members_list,
)
from app.models.authorization import (
    CaseAccessLevel,
    Permissions,
    WarRoomAccessLevel,
)
from app.models.errors import ObjectNotFoundError
from app.models.pagination_parameters import PaginationParameters
from app.schema.marshables import (
    AlertClusterSchema,
    AlertSchema,
    CaseAssetsSchema,
    CaseEvidenceSchema,
    CaseNoteSchema,
    CaseSchemaForAPIV2,
    CaseTaskSchema,
    EventSchema,
    IocSchemaForAPIV2,
    UserSchemaForAPIV2,
)


def _require_case_access(case_id: int) -> None:
    if not ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
        raise MCPError(protocol.IRIS_ACCESS_DENIED,
                       f'No access to case #{case_id}.')


def _require_war_room_access(war_room_id: int) -> None:
    if not ac_fast_check_current_user_has_war_room_access(
            war_room_id,
            [WarRoomAccessLevel.read_only, WarRoomAccessLevel.full_access]):
        raise MCPError(protocol.IRIS_ACCESS_DENIED,
                       f'No access to war room #{war_room_id}.')


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


# ---- Extended case-scoped resources -------------------------------------

@mcp_resource(
    uri_template='iris://cases/{case_id}/evidences',
    name='iris_case_evidences',
    description='Evidences attached to a case.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_evidences(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    result = evidences_filter(case_id, PaginationParameters(1, 100, None, 'asc'))
    return {'data': CaseEvidenceSchema().dump(result.items, many=True),
            'total': result.total}


@mcp_resource(
    uri_template='iris://cases/{case_id}/events',
    name='iris_case_events',
    description='Timeline events attached to a case (chronological).',
    permissions=(Permissions.standard_user,),
)
def _resource_case_events(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    # events_list_filtered treats per_page=0 as "no pagination cap"; we
    # cap at 200 here to keep the resource read bounded.
    result = events_list_filtered(case_id, filters={}, page=1, per_page=200)
    return {'data': EventSchema().dump(result.items, many=True),
            'total': result.total}


@mcp_resource(
    uri_template='iris://cases/{case_id}/timelines',
    name='iris_case_timelines',
    description='Grouped timelines defined on a case.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_timelines(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    # `case_timeline_list` returns a list of plain dicts already — no
    # schema needed. Same shape the REST endpoint serves.
    return {'timelines': list(case_timeline_list(case_id))}


@mcp_resource(
    uri_template='iris://cases/{case_id}/activities',
    name='iris_case_activities',
    description='Activity-log entries for a case (analyst / MCP audit trail).',
    permissions=(Permissions.standard_user,),
)
def _resource_case_activities(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    activities = activity_search_in_case(case_id)
    # `activity_search_in_case` returns Row-like tuples; the REST
    # handler uses `._asdict()` to flatten them, mirror that.
    return {
        'activities': [
            row._asdict() if hasattr(row, '_asdict') else dict(row)
            for row in activities
        ],
    }


@mcp_resource(
    uri_template='iris://cases/{case_id}/war-rooms',
    name='iris_case_war_rooms',
    description='War rooms this case is attached to.',
    permissions=(Permissions.standard_user,),
)
def _resource_case_war_rooms(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    try:
        case = cases_get_by_identifier(case_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    # `case.war_rooms` is the relationship the REST handler dumps via
    # `serialize_case_war_room_summary`. Access is already gated by the
    # per-case ACL check above.
    return {
        'war_rooms': [
            serialize_case_war_room_summary(r)
            for r in getattr(case, 'war_rooms', [])
        ],
    }


@mcp_resource(
    uri_template='iris://cases/{case_id}/source-alert-cluster',
    name='iris_case_source_alert_cluster',
    description=(
        'Alert cluster this case was created from (via escalate/merge). '
        'Empty payload when the case was not sourced from a cluster.'
    ),
    permissions=(Permissions.standard_user,),
)
def _resource_case_source_alert_cluster(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    cluster = alert_clusters_get_by_case(case_id)
    if cluster is None:
        return {'cluster': None}
    return {'cluster': AlertClusterSchema().dump(cluster)}


@mcp_resource(
    uri_template='iris://cases/{case_id}/followers',
    name='iris_case_followers',
    description='Users following this case (dashboard star / mention scope).',
    permissions=(Permissions.standard_user,),
)
def _resource_case_followers(params: dict) -> dict:
    case_id = int(params['case_id'])
    _require_case_access(case_id)
    try:
        case = cases_get_by_identifier(case_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Case not found.') from exc
    # Same relationship traversal the REST /cases/<id>/followers uses.
    followers = getattr(case, 'followers', []) or []
    return {
        'followers': [
            {
                'user_id': getattr(f, 'user_id', None),
                'user_login': getattr(getattr(f, 'user', None), 'user', None),
                'user_name': getattr(getattr(f, 'user', None), 'name', None),
                'created_at': (
                    f.created_at.isoformat() if getattr(f, 'created_at', None) else None
                ),
            }
            for f in followers
        ],
    }


# ---- Alert clusters -----------------------------------------------------

@mcp_resource(
    uri_template='iris://alert-clusters/{cluster_id}',
    name='iris_alert_cluster',
    description='Full alert-cluster record (grouping of related alerts).',
    permissions=(Permissions.alert_clusters_read,),
)
def _resource_alert_cluster(params: dict) -> dict:
    cluster_id = int(params['cluster_id'])
    try:
        cluster = alert_clusters_get(
            iris_current_user,
            ac_current_user_permissions_mask(),
            cluster_id,
            fallback_customer_access=ac_current_user_has_customer_access,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       'Alert cluster not found or access denied.') from exc
    return AlertClusterSchema().dump(cluster)


@mcp_resource(
    uri_template='iris://alert-clusters/{cluster_id}/graph',
    name='iris_alert_cluster_graph',
    description=(
        'Correlation graph payload for an alert cluster — nodes/edges of '
        'the alerts, IOCs, assets, and cases inside the cluster.'
    ),
    permissions=(Permissions.alert_clusters_read,),
)
def _resource_alert_cluster_graph(params: dict) -> dict:
    cluster_id = int(params['cluster_id'])
    try:
        cluster = alert_clusters_get(
            iris_current_user,
            ac_current_user_permissions_mask(),
            cluster_id,
            fallback_customer_access=ac_current_user_has_customer_access,
        )
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS,
                       'Alert cluster not found or access denied.') from exc
    in_dark_mode = bool(getattr(iris_current_user, 'in_dark_mode', False))
    return alert_cluster_correlation_graph(cluster, in_dark_mode)


# ---- War rooms ----------------------------------------------------------
#
# War-room resources scope on `war_room_id` and use the shared
# `serialize_war_room` helpers so the shape stays 1:1 with the v2 REST
# endpoints. Access is enforced via `_require_war_room_access` inside
# each handler (the permission decorator only requires the base
# war-rooms-read permission — per-room ACL is finer-grained).


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}',
    name='iris_war_room',
    description='Full war-room record.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    try:
        room = war_room_get(war_room_id)
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'War room not found.') from exc
    return serialize_war_room(room)


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/members',
    name='iris_war_room_members',
    description='Members of a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_members(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {
        'members': [
            serialize_member(m) for m in war_room_members_list(war_room_id)
        ],
    }


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/cases',
    name='iris_war_room_cases',
    description='Cases attached to a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_cases(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {
        'cases': [
            serialize_case_attachment(r) for r in war_room_cases_list(war_room_id)
        ],
    }


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/chat',
    name='iris_war_room_chat',
    description='Most recent chat messages in a war room (up to 100).',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_chat(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    messages = list_messages(war_room_id, limit=100)
    # `list_messages` returns plain dicts already shaped for the REST
    # endpoint — no schema call needed.
    return {'messages': list(messages)}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/topics',
    name='iris_war_room_topics',
    description='Chat topics defined on a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_topics(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {'topics': [dict(t) for t in list_topics(war_room_id)]}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/notes',
    name='iris_war_room_notes',
    description='Notes attached to a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_notes(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {'notes': list(war_room_note_list(war_room_id))}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/tasks',
    name='iris_war_room_tasks',
    description='Tasks attached to a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_tasks(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {'tasks': list(war_room_task_list(war_room_id))}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/timelines',
    name='iris_war_room_timelines',
    description='Timelines defined on a war room plus their events.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_timelines(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {
        'timelines': list(list_timelines(war_room_id)),
        'events': list(list_timeline_events(war_room_id)),
    }


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/sitreps',
    name='iris_war_room_sitreps',
    description='Situation reports drafted inside a war room.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_sitreps(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {'sitreps': list(sitrep_list(war_room_id))}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/teams',
    name='iris_war_room_teams',
    description='Teams defined inside a war room (@team mentions).',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_teams(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {'teams': list(war_room_team_list(war_room_id))}


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/datastore',
    name='iris_war_room_datastore',
    description='Files uploaded to a war room datastore.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_datastore(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {
        'files': [
            dict(row) if not hasattr(row, '_asdict') else row._asdict()
            for row in war_room_datastore_list(war_room_id)
        ],
    }


@mcp_resource(
    uri_template='iris://war-rooms/{war_room_id}/linked-case-timelines',
    name='iris_war_room_linked_case_timelines',
    description='Case timelines linked to a war room for cross-case correlation.',
    permissions=(Permissions.war_rooms_read,),
)
def _resource_war_room_linked_case_timelines(params: dict) -> dict:
    war_room_id = int(params['war_room_id'])
    _require_war_room_access(war_room_id)
    return {
        'timelines': list(
            list_linked_case_timelines(war_room_id, iris_current_user.id)
        ),
    }
