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

"""War-room note sharing with attached cases (mirror / copy).

Authorization (this layer only):
  * reads need war-room read; targets in cases the caller cannot read
    are returned as `{case_id, accessible: false, status}` — no names;
  * create needs war-room write + `full_access` on every *current*
    target case. For `scope: 'cases'` a listed case without full access
    is a 403. For `scope: 'all'` the caller would be writing into every
    attached case, so missing full access on any of them is a 400 that
    only reports the count (`You lack full access on N attached
    case(s)`) — naming the cases would leak them, and silently
    narrowing 'all' would store a share whose meaning differs from what
    the caller asked for;
  * update needs war-room write + `full_access` on the cases it ADDS
    (same 403 / 400 rule); delete and resync need war-room write only:
    they never put new content into a case, and mirrors are owned by
    the war room.
"""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_api_return_access_denied
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.business.war_room_note_shares import war_room_note_shares_cases_info
from app.business.war_room_note_shares import war_room_note_shares_create
from app.business.war_room_note_shares import war_room_note_shares_current_targets
from app.business.war_room_note_shares import war_room_note_shares_delete
from app.business.war_room_note_shares import war_room_note_shares_describe
from app.business.war_room_note_shares import war_room_note_shares_get
from app.business.war_room_note_shares import war_room_note_shares_list
from app.business.war_room_note_shares import war_room_note_shares_parse_create
from app.business.war_room_note_shares import war_room_note_shares_parse_source
from app.business.war_room_note_shares import war_room_note_shares_parse_update
from app.business.war_room_note_shares import war_room_note_shares_resolve_targets
from app.business.war_room_note_shares import war_room_note_shares_resync
from app.business.war_room_note_shares import war_room_note_shares_source_notes
from app.business.war_room_note_shares import war_room_note_shares_update
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_note_shares_blueprint = Blueprint(
    'war_rooms_note_shares_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/note-shares'
)

_MAX_QUERY_CASE_IDS = 500


class _CaseAccess:
    """Per-request memo of the caller's access on cases."""

    def __init__(self):
        self._read = {}
        self._write = {}

    def can_read(self, case_id):
        if case_id not in self._read:
            self._read[case_id] = ac_fast_check_current_user_has_case_access(
                case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
            ) is not None
        return self._read[case_id]

    def can_write(self, case_id):
        if case_id not in self._write:
            self._write[case_id] = ac_fast_check_current_user_has_case_access(
                case_id, [CaseAccessLevel.full_access]
            ) is not None
        return self._write[case_id]


def _customers_list(customers):
    return [
        {'customer_id': customer_id, 'customer_name': name}
        for customer_id, name in sorted(customers.items())
    ]


def _redact(described, access):
    """Strip names of targets the caller cannot read; add `accessible`
    and the distinct `customers` of the readable targets."""
    for share in described:
        customers = {}
        targets = []
        for target in share['targets']:
            case_id = target['case_id']
            if not access.can_read(case_id):
                targets.append({'case_id': case_id, 'accessible': False,
                                'status': target['status']})
                continue
            target['accessible'] = True
            targets.append(target)
            if target.get('customer_id') is not None:
                customers[target['customer_id']] = target.get('customer_name')
        share['targets'] = targets
        share['customers'] = _customers_list(customers)
    return described


def _denied_targets(scope, target_case_ids, access):
    """None when the caller has full access on every target, else the
    error response (403 for listed cases, 400 with a count for 'all')."""
    denied = [case_id for case_id in target_case_ids if not access.can_write(case_id)]
    if not denied:
        return None
    if scope == 'all':
        return response_api_error(
            f'You lack full access on {len(denied)} attached case(s); '
            f'share with selected cases instead'
        )
    return ac_api_return_access_denied(caseid=denied[0])


def _json_body():
    raw = request.get_json(silent=True)
    return raw if isinstance(raw, dict) else None


def _query_int(name):
    value = request.args.get(name)
    if value is None or value == '':
        return None
    try:
        return int(value)
    except ValueError as e:
        raise BusinessProcessingError(f'{name} must be an integer') from e


def _query_case_ids():
    value = request.args.get('case_ids')
    if not value:
        return []
    parts = [part for part in value.split(',') if part.strip()]
    if len(parts) > _MAX_QUERY_CASE_IDS:
        raise BusinessProcessingError(f'At most {_MAX_QUERY_CASE_IDS} cases per share')
    try:
        return [int(part) for part in parts]
    except ValueError as e:
        raise BusinessProcessingError('case_ids must be a comma-separated list of case ids') from e


def _describe_one(war_room_id, share, access, copy_results=None):
    described = war_room_note_shares_describe(war_room_id, [share], copy_results=copy_results)
    return _redact(described, access)[0]


@war_rooms_note_shares_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='List war room note shares')
def list_note_shares(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        note_id = _query_int('note_id')
        folder_id = _query_int('folder_id')
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    shares = war_room_note_shares_list(war_room_id, note_id=note_id, folder_id=folder_id)
    described = war_room_note_shares_describe(war_room_id, shares)
    return response_api_success(_redact(described, _CaseAccess()))


@war_rooms_note_shares_blueprint.get('/preview')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='Preview a war room note share')
def preview_note_share(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        note_id, folder_id = war_room_note_shares_parse_source(
            war_room_id, _query_int('note_id'), _query_int('folder_id'))
        scope = request.args.get('scope', 'all')
        if scope not in ('all', 'cases'):
            raise BusinessProcessingError("scope must be 'all' or 'cases'")
        case_ids = _query_case_ids()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    access = _CaseAccess()
    target_ids = war_room_note_shares_resolve_targets(war_room_id, scope, case_ids)
    readable = [case_id for case_id in target_ids if access.can_read(case_id)]
    info = war_room_note_shares_cases_info(readable)
    targets = []
    customers = {}
    for case_id in readable:
        case_info = info.get(case_id, {})
        targets.append({
            'case_id': case_id,
            'case_name': case_info.get('case_name'),
            'customer_name': case_info.get('customer_name'),
            'can_write': access.can_write(case_id),
        })
        if case_info.get('customer_id') is not None:
            customers[case_info['customer_id']] = case_info.get('customer_name')
    notes = war_room_note_shares_source_notes(war_room_id, note_id, folder_id)
    return response_api_success({
        'notes': [{'note_id': note.note_id, 'title': note.title} for note in notes],
        'targets': targets,
        'inaccessible_count': len(target_ids) - len(readable),
        'customers': _customers_list(customers),
    })


@war_rooms_note_shares_blueprint.post('')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='Share war room notes with cases',
         response_shape='created')
def create_note_share(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        parsed = war_room_note_shares_parse_create(war_room_id, raw)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    access = _CaseAccess()
    targets = war_room_note_shares_resolve_targets(war_room_id, parsed['scope'], parsed['case_ids'])
    denied = _denied_targets(parsed['scope'], targets, access)
    if denied is not None:
        return denied
    if parsed['delivery'] == 'copy' and not targets:
        return response_api_error('No attached case to copy into')

    try:
        share, copy_results = war_room_note_shares_create(war_room_id, parsed, iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_created(_describe_one(war_room_id, share, access, copy_results))


@war_rooms_note_shares_blueprint.patch('/<int:share_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='Update a war room note share')
def update_note_share(war_room_id, share_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = _json_body()
    if raw is None:
        return response_api_error('Invalid request')
    try:
        share = war_room_note_shares_get(war_room_id, share_id)
        before = set(war_room_note_shares_current_targets(war_room_id, share))
        parsed = war_room_note_shares_parse_update(war_room_id, share, raw)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())

    access = _CaseAccess()
    after = war_room_note_shares_resolve_targets(war_room_id, parsed['scope'], parsed['case_ids'])
    added = [case_id for case_id in after if case_id not in before]
    denied = _denied_targets(parsed['scope'], added, access)
    if denied is not None:
        return denied

    try:
        share = war_room_note_shares_update(war_room_id, share, parsed, iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_describe_one(war_room_id, share, access))


@war_rooms_note_shares_blueprint.delete('/<int:share_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='Delete a war room note share',
         response_shape='deleted')
def delete_note_share(war_room_id, share_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        share = war_room_note_shares_get(war_room_id, share_id)
        war_room_note_shares_delete(war_room_id, share, iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_deleted()


@war_rooms_note_shares_blueprint.post('/<int:share_id>/resync')
@ac_api_requires()
@api_doc(tags=['WarRoomNoteShares'], summary='Re-synchronise a war room note share')
def resync_note_share(war_room_id, share_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        share = war_room_note_shares_get(war_room_id, share_id)
        share = war_room_note_shares_resync(war_room_id, share, iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    return response_api_success(_describe_one(war_room_id, share, _CaseAccess()))
