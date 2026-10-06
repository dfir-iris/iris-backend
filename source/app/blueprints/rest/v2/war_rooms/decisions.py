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

"""War-room decision register (D-n) with target date & time."""

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
from app.business.war_room_chat import emit_system_event
from app.business.war_room_decisions import PATCHABLE_FIELDS
from app.business.war_room_decisions import war_room_decisions_asset_case_ids
from app.business.war_room_decisions import war_room_decisions_chat_candidates
from app.business.war_room_decisions import war_room_decisions_create
from app.business.war_room_decisions import war_room_decisions_delete
from app.business.war_room_decisions import war_room_decisions_get
from app.business.war_room_decisions import war_room_decisions_is_approver
from app.business.war_room_decisions import war_room_decisions_linked_asset_ids
from app.business.war_room_decisions import war_room_decisions_linked_case_ids
from app.business.war_room_decisions import war_room_decisions_list
from app.business.war_room_decisions import war_room_decisions_parse_asset_ids
from app.business.war_room_decisions import war_room_decisions_parse_case_ids
from app.business.war_room_decisions import war_room_decisions_referenced_case_ids
from app.business.war_room_decisions import war_room_decisions_serialize
from app.business.war_room_decisions import war_room_decisions_set_implemented
from app.business.war_room_decisions import war_room_decisions_update
from app.business.war_room_decisions import war_room_decisions_vote
from app.models.authorization import CaseAccessLevel
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_decisions_blueprint = Blueprint(
    'war_rooms_decisions_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/decisions'
)

_REF_TYPE = 'war_room_decision'


def _readable_case_ids(case_ids):
    return {
        case_id for case_id in case_ids
        if ac_fast_check_current_user_has_case_access(
            case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
        ) is not None
    }


def _serialize_many(decisions):
    readable = _readable_case_ids(war_room_decisions_referenced_case_ids(decisions))
    return war_room_decisions_serialize(decisions, readable)


def _serialize_one(decision):
    return _serialize_many([decision])[0]


def _check_asset_access(asset_ids):
    """Linking a case asset requires read access on its case. Unknown
    and unreadable assets get the same answer so ids can't be probed."""
    if not asset_ids:
        return None
    mapping = war_room_decisions_asset_case_ids(asset_ids)
    readable = _readable_case_ids(set(mapping.values()))
    for asset_id in asset_ids:
        if mapping.get(asset_id) not in readable:
            return response_api_error(f'Asset #{asset_id} does not belong to an attached case')
    return None


def _check_case_access(case_ids):
    """Linking a case requires read access on it. Unreadable cases get the
    same answer as unattached ones so ids can't be probed."""
    if not case_ids:
        return None
    readable = _readable_case_ids(case_ids)
    for case_id in case_ids:
        if case_id not in readable:
            return response_api_error(f'Case #{case_id} is not attached to this war room')
    return None


def _parse_status_filter(raw_values):
    out = []
    for raw in raw_values:
        out.extend(p.strip() for p in str(raw).split(',') if p.strip())
    return out


def _emit_decision_event(war_room_id, decision, kind, body):
    emit_system_event(
        war_room_id, kind, body,
        author_id=iris_current_user.id,
        ref_type=_REF_TYPE, ref_id=decision.decision_id,
    )


@war_rooms_decisions_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='List the decisions of a war room',
         query_params=[('status', 'string[]', 'Filter on status (repeatable or CSV)'),
                       ('q', 'string', 'Search in titles'),
                       ('case_id', 'integer', 'Only decisions linked to this case')])
def list_decisions(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    decisions = war_room_decisions_list(
        war_room_id,
        status=_parse_status_filter(request.args.getlist('status')),
        q=request.args.get('q'),
        case_id=request.args.get('case_id', type=int),
    )
    return response_api_success(_serialize_many(decisions))


@war_rooms_decisions_blueprint.get('/chat-candidates')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='List /decision chat messages not yet in the register')
def list_chat_candidates(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success(war_room_decisions_chat_candidates(war_room_id))


@war_rooms_decisions_blueprint.get('/<int:decision_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='Get a war room decision')
def get_decision(war_room_id, decision_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    try:
        decision = war_room_decisions_get(war_room_id, decision_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(_serialize_one(decision))


@war_rooms_decisions_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomDecisions'], summary='Register a war room decision')
def create_decision(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        err = _check_asset_access(war_room_decisions_parse_asset_ids(raw.get('asset_ids'))) \
            or _check_case_access(war_room_decisions_parse_case_ids(raw.get('case_ids')))
        if err is not None:
            return err
        decision = war_room_decisions_create(war_room_id, raw, created_by_id=iris_current_user.id)
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    _emit_decision_event(war_room_id, decision, 'decision', f'D-{decision.number} {decision.title}')
    return response_api_created(_serialize_one(decision))


@war_rooms_decisions_blueprint.patch('/<int:decision_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='Update a war room decision')
def update_decision(war_room_id, decision_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    fields = {k: raw[k] for k in raw if k in PATCHABLE_FIELDS}
    try:
        decision = war_room_decisions_get(war_room_id, decision_id)
        previous_status = decision.status
        if 'asset_ids' in fields:
            asset_ids = war_room_decisions_parse_asset_ids(fields['asset_ids'])
            # Keeping an already-linked asset needs no case access: the
            # caller only sees its id and sends it back untouched.
            already_linked = war_room_decisions_linked_asset_ids(decision.decision_id)
            err = _check_asset_access([a for a in asset_ids if a not in already_linked])
            if err is not None:
                return err
        if 'case_ids' in fields:
            case_ids = war_room_decisions_parse_case_ids(fields['case_ids'])
            already_linked_cases = war_room_decisions_linked_case_ids(decision.decision_id)
            err = _check_case_access([c for c in case_ids if c not in already_linked_cases])
            if err is not None:
                return err
        decision = war_room_decisions_update(war_room_id, decision_id, fields,
                                             updated_by_id=iris_current_user.id)
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    if decision.status != previous_status and decision.status in ('approved', 'rejected'):
        _emit_decision_event(war_room_id, decision, 'system',
                             f'D-{decision.number} {decision.status}: {decision.title}')
    return response_api_success(_serialize_one(decision))


@war_rooms_decisions_blueprint.post('/<int:decision_id>/vote')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='Vote on a war room decision')
def vote_decision(war_room_id, decision_id):
    # Read access is enough: being a listed approver is the gate.
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return response_api_error('Invalid request')
    try:
        decision = war_room_decisions_get(war_room_id, decision_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    if not war_room_decisions_is_approver(decision, iris_current_user.id):
        return ac_api_return_access_denied()
    try:
        decision, status_changed = war_room_decisions_vote(
            war_room_id, decision_id, iris_current_user.id,
            raw.get('verdict'), raw.get('comment'),
        )
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message())
    if status_changed:
        _emit_decision_event(war_room_id, decision, 'system',
                             f'D-{decision.number} {decision.status}: {decision.title}')
    return response_api_success(_serialize_one(decision))


@war_rooms_decisions_blueprint.post('/<int:decision_id>/implemented')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='Mark a war room decision as implemented')
def implement_decision(war_room_id, decision_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        decision = war_room_decisions_set_implemented(war_room_id, decision_id,
                                                      iris_current_user.id, True)
    except ObjectNotFoundError:
        return response_api_not_found()
    _emit_decision_event(war_room_id, decision, 'system',
                         f'D-{decision.number} implemented: {decision.title}')
    return response_api_success(_serialize_one(decision))


@war_rooms_decisions_blueprint.post('/<int:decision_id>/reopen')
@ac_api_requires()
@api_doc(tags=['WarRoomDecisions'], summary='Clear the implemented mark of a war room decision')
def reopen_decision(war_room_id, decision_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        decision = war_room_decisions_set_implemented(war_room_id, decision_id,
                                                      iris_current_user.id, False)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_success(_serialize_one(decision))


@war_rooms_decisions_blueprint.delete('/<int:decision_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomDecisions'], summary='Delete a war room decision')
def delete_decision(war_room_id, decision_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    try:
        war_room_decisions_delete(war_room_id, decision_id)
    except ObjectNotFoundError:
        return response_api_not_found()
    return response_api_deleted()
