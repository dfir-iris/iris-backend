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

"""War-room scope: assets / IOCs of the attached cases, push to cases,
bulk flags, staging area and IOC export.

Authorization lives here: every route checks war-room access first, then
computes the attached cases the caller can read (`read_only|full_access`)
and, for writes, the subset it has `full_access` on. The business layer
only ever sees these explicit case-id lists, so objects of cases the
caller cannot read are never listed, copied or touched; targets that are
not allowed come back as `denied` rows without any case name.
"""

import io

from flask import Blueprint
from flask import request
from flask import send_file

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_requires_vulnerabilities
from app.blueprints.access_controls import ac_current_user_can_read_vulnerabilities
from app.blueprints.access_controls import ac_fast_check_current_user_has_cases_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.war_room_scope import war_room_scope_attached_case_ids
from app.business.war_room_vulnerabilities import war_room_vulnerabilities_matrix
from app.business.war_room_scope import war_room_scope_bulk_flag
from app.business.war_room_scope import war_room_scope_create_asset
from app.business.war_room_scope import war_room_scope_create_ioc
from app.business.war_room_scope import war_room_scope_export_iocs
from app.business.war_room_scope import war_room_scope_list_assets
from app.business.war_room_scope import war_room_scope_list_iocs
from app.business.war_room_scope import war_room_scope_push_assets
from app.business.war_room_scope import war_room_scope_push_iocs
from app.business.war_room_scope import war_room_scope_serialize_staged
from app.business.war_room_scope import war_room_scope_staged_create
from app.business.war_room_scope import war_room_scope_staged_delete
from app.business.war_room_scope import war_room_scope_staged_list
from app.business.war_room_scope import war_room_scope_staged_push
from app.business.war_room_scope import war_room_scope_staged_update
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_scope_blueprint = Blueprint(
    'war_rooms_scope_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/scope'
)


def _readable_case_ids(war_room_id):
    """Attached cases the caller can read, in attachment order.

    One batched access check, whatever the number of attached cases."""
    attached = war_room_scope_attached_case_ids(war_room_id)
    granted = ac_fast_check_current_user_has_cases_access(
        attached, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    )
    return [case_id for case_id in attached if case_id in granted]


def _writable_case_ids(readable_case_ids):
    """Subset of `readable_case_ids` the caller has full access on."""
    granted = ac_fast_check_current_user_has_cases_access(readable_case_ids, [CaseAccessLevel.full_access])
    return [case_id for case_id in readable_case_ids if case_id in granted]


def _json_body():
    raw = request.get_json(silent=True)
    return raw if isinstance(raw, dict) else None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@war_rooms_scope_blueprint.get('/assets')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='List the assets of the attached cases the caller can read',
         query_params=[('q', 'string', 'Substring of the name, IP, domain, description or tags'),
                       ('flag_id', 'string', 'Only the assets carrying this flag, or none for unflagged assets'),
                       ('without_flag_id', 'integer', 'Only the assets not carrying this flag'),
                       ('case_id', 'integer', 'Only the assets of this attached case'),
                       ('compromised', 'boolean', 'Only compromised assets'),
                       ('vulnerable', 'string',
                        'open (an open vulnerability finding), exploited (an exploited one) or none'),
                       ('vulnerability', 'string',
                        'Only the assets with a finding on this vulnerability identifier or alias'),
                       ('page', 'integer',
                        'Page number (1-based). When set, the response also carries total, page, per_page, '
                        'case_totals, flag_totals and per-asset sightings; without it, the list is capped'),
                       ('per_page', 'integer', 'Page size (default 100, max 500), with page'),
                       ('sort', 'string', 'name (default) or case, with page')])
def list_scope_assets(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        result = war_room_scope_list_assets(
            _readable_case_ids(war_room_id),
            search=request.args.get('q'),
            flag=request.args.get('flag_id'),
            without_flag=request.args.get('without_flag_id'),
            case_id=request.args.get('case_id'),
            compromised=request.args.get('compromised'),
            vulnerable=request.args.get('vulnerable'),
            vulnerability=request.args.get('vulnerability'),
            page=request.args.get('page'),
            per_page=request.args.get('per_page'),
            sort=request.args.get('sort'),
            include_vulnerabilities=ac_current_user_can_read_vulnerabilities(),
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.get('/vulnerabilities')
@ac_api_requires()
@ac_api_requires_vulnerabilities()
@api_doc(tags=['WarRoomScope'],
         summary='One page of the vulnerability x case matrix over the attached cases the caller can read, '
                 'worst first, plus the vulnerabilities the war room tracks',
         query_params=[('page', 'integer', 'Page number (1-based, default 1)'),
                       ('per_page', 'integer', 'Vulnerabilities per page (default 50, max 200)'),
                       ('search', 'string', 'Substring of the identifier or title'),
                       ('case_id', 'integer', 'Only the vulnerabilities found in this attached case'),
                       ('tracked', 'boolean', 'Only the vulnerabilities the war room tracks')])
def list_scope_vulnerabilities(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        result = war_room_vulnerabilities_matrix(war_room_id, _readable_case_ids(war_room_id), request.args)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.get('/iocs')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='List the IOCs of the attached cases the caller can read',
         query_params=[('q', 'string', 'Substring of the value, description or tags'),
                       ('case_id', 'integer', 'Only the IOCs of this attached case'),
                       ('page', 'integer',
                        'Page number (1-based) over the distinct indicators. When set, the response also '
                        'carries total, page, per_page and case_totals; without it, the list is capped'),
                       ('per_page', 'integer', 'Indicators per page (default 100, max 500), with page'),
                       ('sort', 'string', 'value (default) or spread (seen in the most cases first), with page')])
def list_scope_iocs(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        result = war_room_scope_list_iocs(
            _readable_case_ids(war_room_id),
            search=request.args.get('q'),
            case_id=request.args.get('case_id'),
            page=request.args.get('page'),
            per_page=request.args.get('per_page'),
            sort=request.args.get('sort'),
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.get('/iocs/export')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Export the IOCs of the readable attached cases as a blocklist (txt, csv, stix)')
def export_scope_iocs(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    export_format = (request.args.get('format') or 'txt').strip().lower()
    include_red = (request.args.get('include_red') or '').strip().lower() in ('1', 'true')
    try:
        content, mimetype, extension = war_room_scope_export_iocs(
            _readable_case_ids(war_room_id), export_format, include_red=include_red,
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    response = send_file(
        io.BytesIO(content.encode('utf-8')),
        mimetype=mimetype,
        as_attachment=True,
        download_name=f'war-room-{war_room_id}-iocs.{extension}',
    )
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------------------------------------------------------------------------
# Create / push into cases
# ---------------------------------------------------------------------------

@war_rooms_scope_blueprint.post('/assets')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Create an asset in one or more attached cases')
def create_scope_asset(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    writable = _writable_case_ids(_readable_case_ids(war_room_id))
    try:
        result = war_room_scope_create_asset(
            war_room_id, iris_current_user, raw.get('asset'), raw.get('case_ids'), writable,
            flag_ids=raw.get('flag_ids'), flag_reason=raw.get('flag_reason'),
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.post('/iocs')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Create an IOC in one or more attached cases')
def create_scope_ioc(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    writable = _writable_case_ids(_readable_case_ids(war_room_id))
    try:
        result = war_room_scope_create_ioc(war_room_id, iris_current_user, raw.get('ioc'), raw.get('case_ids'),
                                           writable)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.post('/assets/push')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Copy assets of attached cases into other attached cases')
def push_scope_assets(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    readable = _readable_case_ids(war_room_id)
    writable = _writable_case_ids(readable)
    try:
        result = war_room_scope_push_assets(war_room_id, iris_current_user, raw.get('asset_ids'),
                                            raw.get('case_ids'), readable, writable)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.post('/iocs/push')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Copy IOCs of attached cases into other attached cases')
def push_scope_iocs(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    readable = _readable_case_ids(war_room_id)
    writable = _writable_case_ids(readable)
    try:
        result = war_room_scope_push_iocs(war_room_id, iris_current_user, raw.get('ioc_ids'),
                                          raw.get('case_ids'), readable, writable)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


@war_rooms_scope_blueprint.post('/assets/flags')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Set or remove a status flag on several assets of the attached cases')
def bulk_flag_scope_assets(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    readable = _readable_case_ids(war_room_id)
    writable = _writable_case_ids(readable)
    try:
        result = war_room_scope_bulk_flag(
            war_room_id, iris_current_user.id, raw.get('asset_ids'), raw.get('flag_id'), raw.get('action', 'set'),
            raw.get('reason'), raw.get('decision_id'), readable, writable,
        )
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)


# ---------------------------------------------------------------------------
# Staging area
# ---------------------------------------------------------------------------

@war_rooms_scope_blueprint.get('/staging')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='List the objects staged in the war room')
def list_staged_objects(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success(war_room_scope_staged_list(war_room_id))


@war_rooms_scope_blueprint.post('/staging')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomScope'], summary='Stage an asset or IOC before pushing it to cases')
def create_staged_object(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        staged = war_room_scope_staged_create(war_room_id, iris_current_user.id, raw)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(war_room_scope_serialize_staged(staged))


@war_rooms_scope_blueprint.patch('/staging/<int:staged_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Update a staged object')
def update_staged_object(war_room_id, staged_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        staged = war_room_scope_staged_update(war_room_id, staged_id, raw)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(war_room_scope_serialize_staged(staged))


@war_rooms_scope_blueprint.delete('/staging/<int:staged_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomScope'], summary='Discard a staged object')
def delete_staged_object(war_room_id, staged_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_scope_staged_delete(war_room_id, staged_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()


@war_rooms_scope_blueprint.post('/staging/<int:staged_id>/push')
@ac_api_requires()
@api_doc(tags=['WarRoomScope'], summary='Create a staged object in attached cases')
def push_staged_object(war_room_id, staged_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json(silent=True)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    writable = _writable_case_ids(_readable_case_ids(war_room_id))
    try:
        result = war_room_scope_staged_push(war_room_id, iris_current_user, staged_id, raw.get('case_ids'),
                                            writable)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(result)
