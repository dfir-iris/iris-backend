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

"""Case trackers: the case's steps, its trackers, their rows and row
history, and the tracker templates applied to / saved from a case.

Mounted under `/api/v2/cases/<case_identifier>/tracker-steps` and
`/api/v2/cases/<case_identifier>/trackers`. Reads need read access to the
case, writes full access. Saving a case as a template needs server
administrator, like every template change."""

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
from app.business.case_trackers import case_trackers_create
from app.business.case_trackers import case_trackers_delete
from app.business.case_trackers import case_trackers_get
from app.business.case_trackers import case_trackers_history
from app.business.case_trackers import case_trackers_list
from app.business.case_trackers import case_trackers_rows_add
from app.business.case_trackers import case_trackers_rows_delete
from app.business.case_trackers import case_trackers_rows_list
from app.business.case_trackers import case_trackers_rows_reorder
from app.business.case_trackers import case_trackers_rows_update_state
from app.business.case_trackers import case_trackers_serialize_many
from app.business.case_trackers import case_trackers_serialize_step
from app.business.case_trackers import case_trackers_step_create
from app.business.case_trackers import case_trackers_step_delete
from app.business.case_trackers import case_trackers_step_update
from app.business.case_trackers import case_trackers_steps_list
from app.business.case_trackers import case_trackers_steps_reorder
from app.business.case_trackers import case_trackers_timeline_sync
from app.business.case_trackers import case_trackers_trackers_reorder
from app.business.case_trackers import case_trackers_update
from app.business.cases import cases_exists
from app.business.war_room_tracker_templates import tracker_templates_apply_to_case
from app.business.war_room_tracker_templates import tracker_templates_save_from_case
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


case_tracker_steps_blueprint = Blueprint(
    'case_tracker_steps_rest_v2', __name__,
    url_prefix='/<int:case_identifier>/tracker-steps'
)

case_trackers_blueprint = Blueprint(
    'case_trackers_rest_v2', __name__,
    url_prefix='/<int:case_identifier>/trackers'
)


def _require_read_access(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(
        case_identifier, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
    ):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


def _require_full_access(case_identifier):
    if not cases_exists(case_identifier):
        return response_api_not_found()
    if not ac_fast_check_current_user_has_case_access(case_identifier, [CaseAccessLevel.full_access]):
        return ac_api_return_access_denied(caseid=case_identifier)
    return None


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


def _handle_deleted(operation):
    try:
        operation()
    except ObjectNotFoundError:
        return response_api_not_found()
    except BusinessProcessingError as e:
        return response_api_error(e.get_message(), data=e.get_data())
    return response_api_deleted()


def _serialize_tracker(case_identifier, tracker):
    return case_trackers_serialize_many(case_identifier, [tracker])[0]


def _query_int(name):
    raw = request.args.get(name)
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except ValueError:
        raise BusinessProcessingError(f'{name} must be an integer')
    return value


# ----------------------------------------------------------------- Steps --

@case_tracker_steps_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='List the tracker steps of a case, ordered')
def list_tracker_steps(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return response_api_success([case_trackers_serialize_step(s) for s in case_trackers_steps_list(case_identifier)])


@case_tracker_steps_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['CaseTrackers'],
         summary='Create a tracker step (name unique in the case, case-insensitively)')
def create_tracker_step(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_trackers_serialize_step(case_trackers_step_create(case_identifier, body)),
                   created=True)


@case_tracker_steps_blueprint.post('/reorder')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='Reorder the tracker steps (listed ids first, the others after)')
def reorder_tracker_steps(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: [case_trackers_serialize_step(s)
                            for s in case_trackers_steps_reorder(case_identifier, body.get('ids'))])


@case_tracker_steps_blueprint.patch('/<int:step_id>')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='Update a tracker step (fields left out keep their value)')
def update_tracker_step(case_identifier, step_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_trackers_serialize_step(case_trackers_step_update(case_identifier, step_id, body)))


@case_tracker_steps_blueprint.delete('/<int:step_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['CaseTrackers'],
         summary='Delete a tracker step: removed from every row and from the trackers offering it')
def delete_tracker_step(case_identifier, step_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_trackers_step_delete(case_identifier, step_id))


# -------------------------------------------------------------- Trackers --

@case_trackers_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='List the trackers of a case, with their number of rows')
def list_trackers(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return response_api_success(case_trackers_serialize_many(case_identifier, case_trackers_list(case_identifier)))


@case_trackers_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['CaseTrackers'],
         summary='Create a tracker (manual, or mirroring the assets / iocs / tasks / evidences of the case)')
def create_tracker(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: _serialize_tracker(case_identifier,
                                              case_trackers_create(case_identifier, body, iris_current_user.id)),
                   created=True)


@case_trackers_blueprint.post('/reorder')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='Reorder the trackers (listed ids first, the others after)')
def reorder_trackers(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_trackers_serialize_many(
        case_identifier, case_trackers_trackers_reorder(case_identifier, body.get('ids'))))


@case_trackers_blueprint.post('/apply-template')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'],
         summary='Apply a tracker template: merge its steps by name and create its trackers (those of a war-room '
                 'source are skipped and named in `skipped`). Returns every step of the case and the created trackers')
def apply_tracker_template(case_identifier):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    template_id = _body().get('template_id')
    if not isinstance(template_id, int) or isinstance(template_id, bool):
        return response_api_error('template_id must be an integer')

    def _apply():
        steps, trackers, skipped = tracker_templates_apply_to_case(case_identifier, template_id,
                                                                   iris_current_user.id)
        return {'steps': [case_trackers_serialize_step(s) for s in steps],
                'trackers': case_trackers_serialize_many(case_identifier, trackers),
                'skipped': skipped}
    return _handle(_apply)


@case_trackers_blueprint.post('/save-as-template')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['CaseTrackers'],
         summary='Save the steps and trackers (all, or tracker_ids) of the case as a new tracker template')
def save_trackers_as_template(case_identifier):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: tracker_templates_save_from_case(case_identifier, body, iris_current_user.id),
                   created=True)


@case_trackers_blueprint.get('/<int:tracker_id>')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='Get a tracker')
def get_tracker(case_identifier, tracker_id):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: _serialize_tracker(case_identifier, case_trackers_get(case_identifier, tracker_id)))


@case_trackers_blueprint.patch('/<int:tracker_id>')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'], summary='Update a tracker (fields left out keep their value, the source cannot change)')
def update_tracker(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: _serialize_tracker(case_identifier,
                                              case_trackers_update(case_identifier, tracker_id, body)))


@case_trackers_blueprint.delete('/<int:tracker_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['CaseTrackers'], summary='Delete a tracker with its rows and their history')
def delete_tracker(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_trackers_delete(case_identifier, tracker_id))


# ------------------------------------------------------------------ Rows --

@case_trackers_blueprint.get('/<int:tracker_id>/rows')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'],
         summary='List the rows of a tracker: every current item of the case merged with its state for a mirror, '
                 'the stored rows for a manual tracker (capped)')
def list_tracker_rows(case_identifier, tracker_id):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_trackers_rows_list(case_identifier, case_trackers_get(case_identifier, tracker_id)))


@case_trackers_blueprint.post('/<int:tracker_id>/rows')
@ac_api_requires()
@api_doc(response_shape='created', tags=['CaseTrackers'],
         summary='Add rows to a manual tracker (free text or case items; items already present are skipped unless '
                 'allow_duplicates)')
def add_tracker_rows(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_trackers_rows_add(case_identifier, case_trackers_get(case_identifier, tracker_id),
                                                  body, iris_current_user.id), created=True)


@case_trackers_blueprint.post('/<int:tracker_id>/rows/state')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'],
         summary='Bulk update the steps, note, column values or label (custom label of an item row) of tracker '
                 'rows (one history event per change)')
def update_tracker_rows_state(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: case_trackers_rows_update_state(
        case_identifier, case_trackers_get(case_identifier, tracker_id), body, iris_current_user.id))


@case_trackers_blueprint.post('/<int:tracker_id>/timeline/sync')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'],
         summary='Catch the timeline up with a tracker whose timeline sync was turned on after the fact: the '
                 'current state of the rows (state mode), or the past changes not in the timeline yet, oldest first '
                 'and at most 2000 per call, `remaining` telling what is left (changes mode)')
def sync_tracker_timeline(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_trackers_timeline_sync(
        case_identifier, case_trackers_get(case_identifier, tracker_id), iris_current_user.id))


@case_trackers_blueprint.post('/<int:tracker_id>/rows/reorder')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['CaseTrackers'],
         summary='Reorder the rows of a manual tracker (listed ids first, the others after)')
def reorder_tracker_rows(case_identifier, tracker_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    body = _body()
    return _handle_deleted(lambda: case_trackers_rows_reorder(case_trackers_get(case_identifier, tracker_id),
                                                              body.get('ids')))


@case_trackers_blueprint.get('/<int:tracker_id>/rows/history')
@ac_api_requires()
@api_doc(tags=['CaseTrackers'],
         summary='History of one row (row_id, or item_type and item_id) or the latest events of the tracker',
         query_params=[('row_id', 'integer', 'Events of this row'),
                       ('item_type', 'string', 'Events of the row of this item (with item_id)'),
                       ('item_id', 'integer', 'Events of the row of this item (with item_type)')])
def tracker_rows_history(case_identifier, tracker_id):
    err = _require_read_access(case_identifier)
    if err is not None:
        return err
    return _handle(lambda: case_trackers_history(
        case_trackers_get(case_identifier, tracker_id), row_id=_query_int('row_id'),
        item_type=request.args.get('item_type') or None, item_id=_query_int('item_id')))


@case_trackers_blueprint.delete('/<int:tracker_id>/rows/<int:row_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['CaseTrackers'], summary='Delete a row of a manual tracker with its history')
def delete_tracker_row(case_identifier, tracker_id, row_id):
    err = _require_full_access(case_identifier)
    if err is not None:
        return err
    return _handle_deleted(lambda: case_trackers_rows_delete(
        case_identifier, case_trackers_get(case_identifier, tracker_id), row_id))
