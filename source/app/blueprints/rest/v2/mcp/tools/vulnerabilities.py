#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

"""MCP tools for the vulnerability catalogue and findings.

Same visibility as the REST routes: catalogue counts and exposure cover
the cases the caller can read (and the registry when they hold
`asset_manager_read`), the war-room matrix only the attached cases they
can read, plus the entries the room tracks (which a war-room writer can
add). Recording or changing findings stays in the UI / REST API.

These tools serve the MCP server and the in-app LLM alike, so their
results leave the instance: like case export, they never carry the
content of a private catalogue entry (IRIS-VULN-…), only its identifier,
scoring and where it is found (`withheld: true`). Private entries are
not matched on their content either (title, tags, aliases).
"""
from __future__ import annotations

from app.blueprints.access_controls import ac_current_user_can_create_vulnerabilities
from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.v2.manage_routes.vulnerabilities import vulnerabilities_visibility
from app.blueprints.rest.v2.mcp import protocol
from app.blueprints.rest.v2.mcp.dispatch import MCPError
from app.blueprints.rest.v2.mcp.registry import mcp_tool
from app.blueprints.rest.v2.mcp.tools._common import PAGINATION_SCHEMA_FRAGMENT
from app.blueprints.rest.v2.mcp.tools._common import build_pagination
from app.business.vulnerabilities import vulnerabilities_exposure
from app.business.vulnerabilities import vulnerabilities_get
from app.business.vulnerabilities import vulnerabilities_get_public
from app.business.vulnerabilities import vulnerabilities_lookup
from app.business.vulnerabilities import vulnerabilities_mask_private
from app.business.vulnerabilities import vulnerabilities_search
from app.business.vulnerability_findings import vulnerability_findings_case_list
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_matrix
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_track
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError

_SEVERITY_LIST = {
    'type': 'array',
    'items': {'type': 'string', 'enum': ['critical', 'high', 'medium', 'low', 'none', 'unknown']},
    'description': 'Only these severities.',
}


def _require(create=False):
    allowed = ac_current_user_can_create_vulnerabilities() if create else ac_current_user_can_read_vulnerabilities()
    if not allowed:
        needed = 'vulnerabilities_create' if create else 'vulnerabilities_read'
        raise MCPError(protocol.IRIS_ACCESS_DENIED, f'This tool needs the {needed} permission.')


def _run(operation):
    try:
        return vulnerabilities_mask_private(operation())
    except ObjectNotFoundError as exc:
        raise MCPError(protocol.INVALID_PARAMS, 'Vulnerability not found.') from exc
    except BusinessProcessingError as exc:
        raise MCPError(protocol.INVALID_PARAMS, exc.get_message()) from exc


@mcp_tool(
    name='iris_vulnerabilities_search',
    description=(
        'Search the vulnerability catalogue (CVEs, advisories, private IRIS-VULN entries). Each row '
        'carries its severity, CVSS, KEV flag and how many findings the caller can see (open, fixed, '
        'exploited). The content of private entries is withheld (withheld: true); they only match '
        'on their identifier.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            **PAGINATION_SCHEMA_FRAGMENT,
            'search': {'type': 'string', 'description': 'Substring of the identifier, an alias, the title or tags.'},
            'severity': _SEVERITY_LIST,
            'kev': {'type': 'boolean', 'description': 'Only (or no) CISA KEV entries.'},
            'affected': {'type': 'boolean',
                         'description': 'Only entries with an open finding in a readable case.'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_search(args: dict) -> dict:
    return _run(lambda: vulnerabilities_search(args, build_pagination(args), *vulnerabilities_visibility(),
                                               hide_private_content=True))


@mcp_tool(
    name='iris_vulnerabilities_get',
    description=(
        'Fetch one catalogue entry, by numeric id or by identifier / alias (e.g. CVE-2024-3400), '
        'with the cases and registry assets it affects. The content of a private entry is withheld; '
        'it is only found by its own identifier.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'vulnerability_id': {'type': 'integer'},
            'identifier': {'type': 'string'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    mvp=True,
)
def iris_vulnerabilities_get(args: dict) -> dict:
    def _get():
        case_ids, registry_scope = vulnerabilities_visibility()
        if args.get('vulnerability_id') is not None:
            vulnerability = vulnerabilities_get(args['vulnerability_id'])
            entry = vulnerabilities_get_public(vulnerability, case_ids, registry_scope)
        elif args.get('identifier'):
            entry = vulnerabilities_lookup(args['identifier'], case_ids, registry_scope, hide_private_content=True)
            if entry is None:
                raise ObjectNotFoundError()
            vulnerability = vulnerabilities_get(entry['vulnerability_id'])
        else:
            raise BusinessProcessingError('vulnerability_id or identifier is required')
        entry['exposure'] = vulnerabilities_exposure(vulnerability, case_ids, registry_scope)
        return entry

    return _run(_get)


@mcp_tool(
    name='iris_case_vulnerabilities_list',
    description=(
        'List the vulnerability findings of a case, worst first: asset, remediation status '
        '(affected, patched, mitigated, risk-accepted…), exploitation status, due date, evidence.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'status_group': {'type': 'string', 'enum': ['open', 'fixed', 'dismissed']},
            'severity': _SEVERITY_LIST,
            'exploitation': {
                'type': 'array',
                'items': {'type': 'string',
                          'enum': ['unknown', 'not-exploited', 'attempted', 'suspected', 'exploited']},
            },
            'asset_id': {'type': 'integer', 'description': 'Only the findings of this asset.'},
            'overdue': {'type': 'boolean', 'description': 'Only open findings past their due date.'},
            'search': {'type': 'string'},
        },
    },
    permissions=(Permissions.vulnerabilities_read, Permissions.server_administrator),
    case_scoped=True,
    mvp=True,
)
def iris_case_vulnerabilities_list(args: dict) -> dict:
    return _run(lambda: vulnerability_findings_case_list(args['case_identifier'], args))


@mcp_tool(
    name='iris_war_room_vulnerabilities_matrix',
    description=(
        'One page of the vulnerability x case matrix of a war room (attached cases the caller can read, '
        'plus the entries the room tracks): per entry, per case, the findings open / fixed / dismissed / '
        'exploited, exploited-and-open first. `total` is the number of entries over all pages.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'page': {'type': 'integer', 'description': '1-indexed page number.'},
            'per_page': {'type': 'integer', 'description': 'Entries per page (default 50, max 200).'},
            'search': {'type': 'string', 'description': 'Substring of the identifier or title.'},
            'case_id': {'type': 'integer', 'description': 'Only the entries found in this attached case.'},
            'tracked': {'type': 'boolean', 'description': 'Only the entries the war room tracks.'},
        },
    },
    permissions=(Permissions.war_rooms_read, Permissions.server_administrator),
    war_room_scoped=True,
    mvp=True,
)
def iris_war_room_vulnerabilities_matrix(args: dict) -> dict:
    _require()
    attached = war_room_scope_attached_case_ids(args['war_room_id'])
    granted = ac_fast_check_current_user_has_cases_access(
        attached, [CaseAccessLevel.read_only, CaseAccessLevel.full_access])
    readable = [case_id for case_id in attached if case_id in granted]
    query = {key: args.get(key) for key in ('page', 'per_page', 'search', 'case_id', 'tracked')}
    return _run(lambda: war_room_vulnerabilities_matrix(args['war_room_id'], readable, query,
                                                        hide_private_content=True))


@mcp_tool(
    name='iris_war_room_vulnerabilities_track',
    description=(
        'Track a vulnerability on a war room, even when no asset is known to be affected yet. '
        'Identify it by catalogue vulnerability_id or by identifier (CVE, GHSA…; unknown public '
        'identifiers are added to the catalogue). Idempotent; a given note replaces the previous one.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'vulnerability_id': {'type': 'integer'},
            'identifier': {'type': 'string'},
            'note': {'type': 'string'},
        },
    },
    permissions=(Permissions.war_rooms_write, Permissions.server_administrator),
    war_room_scoped=True,
)
def iris_war_room_vulnerabilities_track(args: dict) -> dict:
    _require(create=True)
    body = {key: args.get(key) for key in ('vulnerability_id', 'identifier', 'note')}
    return _run(lambda: war_room_vulnerabilities_track(args['war_room_id'], body, iris_current_user._get_current_object().id)[0])  # type: ignore[attr-defined]
