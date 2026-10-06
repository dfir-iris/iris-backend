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

"""v2 endpoints of the vulnerability catalogue (`/api/v2/manage/vulnerabilities`).

The catalogue is instance-wide: holders of `vulnerabilities_read` read
it, holders of `vulnerabilities_create` add entries (and fetch / sync
them from cve.org). Editing an entry is open to its creator (while they
hold `vulnerabilities_create`), to holders of `vulnerabilities_write`
and to server administrators; deleting and merging entries stays with
server administrators.

Finding counts and the exposure view only ever cover the cases the
caller can read, plus the registry assets they can see when they hold
`asset_manager_read`.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_requires_vulnerabilities
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_current_user_can_create_vulnerabilities
from app.blueprints.access_controls import ac_current_user_has_permission
from app.blueprints.access_controls import ac_current_user_permissions_mask
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.parsing import parse_pagination_parameters
from app.business.managed_assets import managed_assets_viewer_scope
from app.business.vulnerabilities import vulnerabilities_create
from app.business.vulnerabilities import vulnerabilities_delete
from app.business.vulnerabilities import vulnerabilities_exposure
from app.business.vulnerabilities import vulnerabilities_get
from app.business.vulnerabilities import vulnerabilities_get_public
from app.business.vulnerabilities import vulnerabilities_lookup
from app.business.vulnerabilities import vulnerabilities_merge
from app.business.vulnerabilities import vulnerabilities_search
from app.business.vulnerabilities import vulnerabilities_update
from app.business.vulnerabilities_cve_sync import vulnerabilities_cve_lookup
from app.business.vulnerabilities_cve_sync import vulnerabilities_cve_sync
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _handle(operation, created=False):
    try:
        result = operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    if created:
        return response_api_created(result)
    return response_api_success(result)


def vulnerabilities_visibility():
    """`(case_ids, registry_scope)` of the caller: the readable case ids
    (None = all) and the registry scope (None without `asset_manager_read`)."""
    scope = managed_assets_viewer_scope(iris_current_user, ac_current_user_permissions_mask())
    registry_scope = scope if ac_current_user_has_permission(Permissions.asset_manager_read) else None
    return scope.get_case_ids(), registry_scope


def _public(vulnerability):
    return vulnerabilities_get_public(vulnerability, *vulnerabilities_visibility())


def _can_edit(vulnerability):
    return ((vulnerability.created_by_id == iris_current_user.id and ac_current_user_can_create_vulnerabilities())
            or ac_current_user_has_permission(Permissions.vulnerabilities_write)
            or ac_current_user_has_permission(Permissions.server_administrator))


vulnerabilities_blueprint = Blueprint(
    'vulnerabilities_rest_v2', __name__,
    url_prefix='/vulnerabilities'
)


@vulnerabilities_blueprint.get('')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'], summary='Search the vulnerability catalogue',
         query_params=[('page', 'integer', 'Page number, 1-based'),
                       ('per_page', 'integer', 'Rows per page (at most 200)'),
                       ('order_by', 'string',
                        'One of: identifier, title, cvss_score, epss_score, published_at, created_at, '
                        'updated_at, severity'),
                       ('sort_dir', 'string', 'asc or desc'),
                       ('search', 'string', 'Substring of the identifier, an alias, the title or the tags'),
                       ('severity', 'string', 'Comma-separated severities'),
                       ('kind', 'string', 'Comma-separated kinds'),
                       ('kev', 'boolean', 'Only (or no) CISA KEV entries'),
                       ('private', 'boolean', 'Only private (or public) entries'),
                       ('affected', 'boolean', 'Only entries with an open finding in a readable case')])
def list_vulnerabilities_route():
    pagination = parse_pagination_parameters(request, default_order_by='updated_at', default_direction='desc')
    return _handle(lambda: vulnerabilities_search(request.args, pagination, *vulnerabilities_visibility()))


@vulnerabilities_blueprint.get('/lookup')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'], summary='Find the catalogue entry of an identifier or alias (null if unknown)',
         query_params=[('identifier', 'string', 'CVE, GHSA, advisory or IRIS-VULN identifier', True)])
def lookup_vulnerability_route():
    identifier = request.args.get('identifier')
    if not identifier:
        return response_api_error('identifier is required')
    return _handle(lambda: vulnerabilities_lookup(identifier, *vulnerabilities_visibility()))


@vulnerabilities_blueprint.get('/cve-lookup')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['Vulnerabilities'],
         summary='Fetch a CVE from cve.org as catalogue fields, without saving anything',
         query_params=[('identifier', 'string', 'CVE identifier (CVE-YYYY-NNNN)', True)])
def cve_lookup_vulnerability_route():
    identifier = request.args.get('identifier')
    if not identifier:
        return response_api_error('identifier is required')
    return _handle(lambda: vulnerabilities_cve_lookup(identifier))


@vulnerabilities_blueprint.post('')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(response_shape='created', tags=['Vulnerabilities'],
         summary='Create a catalogue entry (private entries get an IRIS-VULN identifier)')
def create_vulnerability_route():
    body = _body()
    return _handle(lambda: _public(vulnerabilities_create(body, iris_current_user.id)), created=True)


@vulnerabilities_blueprint.get('/<int:identifier>')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'], summary='Get a catalogue entry with its finding counts')
def get_vulnerability_route(identifier):
    return _handle(lambda: _public(vulnerabilities_get(identifier)))


@vulnerabilities_blueprint.put('/<int:identifier>')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'], summary='Update a catalogue entry (fields left out keep their value)')
def update_vulnerability_route(identifier):
    try:
        vulnerability = vulnerabilities_get(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    if not _can_edit(vulnerability):
        return ac_api_return_access_denied()
    body = _body()
    return _handle(lambda: _public(vulnerabilities_update(vulnerability, body, iris_current_user.id)))


@vulnerabilities_blueprint.post('/<int:identifier>/sync')
@ac_api_requires()
@ac_api_requires_vulnerabilities(create=True)
@api_doc(tags=['Vulnerabilities'],
         summary='Refresh a public CVE entry from cve.org (local edits are kept unless force is true)')
def sync_vulnerability_route(identifier):
    try:
        vulnerability = vulnerabilities_get(identifier)
    except ObjectNotFoundError:
        return response_api_not_found()
    force = _body().get('force', False)
    if not isinstance(force, bool):
        return response_api_error('force must be a boolean')
    # A plain sync only fills what nobody edited, from the authoritative
    # record: any analyst may run it. Overwriting edits takes edit rights.
    if force and not _can_edit(vulnerability):
        return ac_api_return_access_denied()

    def _sync():
        report = vulnerabilities_cve_sync(vulnerability, iris_current_user.id, force=force)
        result = _public(vulnerability)
        result['sync'] = report
        return result

    return _handle(_sync)


@vulnerabilities_blueprint.delete('/<int:identifier>')
@ac_api_requires(Permissions.server_administrator)
@ac_api_requires_vulnerabilities()
@api_doc(response_shape='deleted', tags=['Vulnerabilities'],
         summary='Delete a catalogue entry that no finding uses')
def delete_vulnerability_route(identifier):
    try:
        vulnerabilities_delete(vulnerabilities_get(identifier))
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()


@vulnerabilities_blueprint.post('/<int:identifier>/merge')
@ac_api_requires(Permissions.server_administrator)
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'],
         summary='Merge an entry into another (findings move, identifiers become aliases)')
def merge_vulnerability_route(identifier):
    body = _body()

    def _merge():
        target, summary = vulnerabilities_merge(vulnerabilities_get(identifier), body, iris_current_user.id)
        result = _public(target)
        result['merge'] = summary
        return result

    return _handle(_merge)


@vulnerabilities_blueprint.get('/<int:identifier>/exposure')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['Vulnerabilities'],
         summary='Cases and registry assets affected by an entry, within what the caller can see')
def get_vulnerability_exposure_route(identifier):
    return _handle(lambda: vulnerabilities_exposure(vulnerabilities_get(identifier), *vulnerabilities_visibility()))
