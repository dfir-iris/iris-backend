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

"""War-room trackers: the room's steps, its trackers, their rows and
row history, and the tracker templates applied to / saved from a room.

Reads need war-room read access, writes war-room write access. What a
caller sees of the rows is bounded by the attached cases it can read and
by its Assets Manager scope (registry assets need asset_manager_read).
Saving a room as a template needs server administrator, like every
template change. A tracker writing to the case timelines only writes in
the readable cases the caller has full access to; the others are skipped
and listed in the `timeline` part of the response. Live updates go out on the `trackers` topic (see
`live.py`); what changed is also posted to the war-room stream as
`tracker` rows."""

from flask import Blueprint
from flask import request

from app.blueprints.access_controls import ac_api_requires
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.v2.war_rooms.access import require_war_room_read
from app.blueprints.rest.v2.war_rooms.access import require_war_room_write
from app.blueprints.rest.v2.war_rooms.access import war_room_tracker_access
from app.business.war_room_chat import emit_system_event
from app.business.war_room_tracker_templates import tracker_templates_apply
from app.business.war_room_tracker_templates import tracker_templates_save_from_war_room
from app.business.war_room_trackers import war_room_trackers_create
from app.business.war_room_trackers import war_room_trackers_delete
from app.business.war_room_trackers import war_room_trackers_describe_changes
from app.business.war_room_trackers import war_room_trackers_describe_rows
from app.business.war_room_trackers import war_room_trackers_get
from app.business.war_room_trackers import war_room_trackers_history
from app.business.war_room_trackers import war_room_trackers_list
from app.business.war_room_trackers import war_room_trackers_rows_add
from app.business.war_room_trackers import war_room_trackers_rows_delete
from app.business.war_room_trackers import war_room_trackers_rows_list
from app.business.war_room_trackers import war_room_trackers_rows_reorder
from app.business.war_room_trackers import war_room_trackers_rows_update_state
from app.business.war_room_trackers import war_room_trackers_serialize_many
from app.business.war_room_trackers import war_room_trackers_serialize_step
from app.business.war_room_trackers import war_room_trackers_step_create
from app.business.war_room_trackers import war_room_trackers_step_delete
from app.business.war_room_trackers import war_room_trackers_step_update
from app.business.war_room_trackers import war_room_trackers_steps_list
from app.business.war_room_trackers import war_room_trackers_steps_reorder
from app.business.war_room_trackers import war_room_trackers_timeline_sync
from app.business.war_room_trackers import war_room_trackers_trackers_reorder
from app.business.war_room_trackers import war_room_trackers_update
from app.models.authorization import Permissions
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


war_rooms_tracker_steps_blueprint = Blueprint(
    'war_rooms_tracker_steps_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/tracker-steps'
)

war_rooms_trackers_blueprint = Blueprint(
    'war_rooms_trackers_rest_v2', __name__,
    url_prefix='/<int:war_room_id>/trackers'
)


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


def _serialize_trackers(war_room_id, trackers):
    return war_room_trackers_serialize_many(war_room_id, trackers, war_room_tracker_access(war_room_id))


def _serialize_tracker(war_room_id, tracker):
    return _serialize_trackers(war_room_id, [tracker])[0]


def _emit_tracker_event(war_room_id, body, tracker_id=None):
    emit_system_event(
        war_room_id, 'tracker', body,
        author_id=iris_current_user.id,
        ref_type='war_room_tracker' if tracker_id is not None else None, ref_id=tracker_id,
    )


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

@war_rooms_tracker_steps_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='List the tracker steps of a war room, ordered')
def list_tracker_steps(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success([war_room_trackers_serialize_step(s) for s in war_room_trackers_steps_list(war_room_id)])


@war_rooms_tracker_steps_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomTrackers'],
         summary='Create a tracker step (name unique in the room, case-insensitively)')
def create_tracker_step(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()

    def _create():
        step = war_room_trackers_step_create(war_room_id, body)
        _emit_tracker_event(war_room_id, f'Created tracker step: {step.name}')
        return war_room_trackers_serialize_step(step)
    return _handle(_create, created=True)


@war_rooms_tracker_steps_blueprint.post('/reorder')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='Reorder the tracker steps (listed ids first, the others after)')
def reorder_tracker_steps(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: [war_room_trackers_serialize_step(s)
                            for s in war_room_trackers_steps_reorder(war_room_id, body.get('ids'))])


@war_rooms_tracker_steps_blueprint.patch('/<int:step_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='Update a tracker step (fields left out keep their value)')
def update_tracker_step(war_room_id, step_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: war_room_trackers_serialize_step(war_room_trackers_step_update(war_room_id, step_id, body)))


@war_rooms_tracker_steps_blueprint.delete('/<int:step_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomTrackers'],
         summary='Delete a tracker step: removed from every row and from the trackers offering it')
def delete_tracker_step(war_room_id, step_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err

    def _delete():
        name = war_room_trackers_step_delete(war_room_id, step_id)
        _emit_tracker_event(war_room_id, f'Deleted tracker step: {name}')
    return _handle_deleted(_delete)


# -------------------------------------------------------------- Trackers --

@war_rooms_trackers_blueprint.get('')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='List the trackers of a war room, with the number of rows the caller sees')
def list_trackers(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return response_api_success(_serialize_trackers(war_room_id, war_room_trackers_list(war_room_id)))


@war_rooms_trackers_blueprint.post('')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomTrackers'],
         summary='Create a tracker (manual, or mirroring the assets / iocs / customers / cases / registry_assets)')
def create_tracker(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()

    def _create():
        tracker = war_room_trackers_create(war_room_id, body, iris_current_user.id)
        _emit_tracker_event(war_room_id, f'Created tracker: {tracker.name}', tracker.id)
        return _serialize_tracker(war_room_id, tracker)
    return _handle(_create, created=True)


@war_rooms_trackers_blueprint.post('/reorder')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='Reorder the trackers (listed ids first, the others after)')
def reorder_trackers(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: _serialize_trackers(war_room_id,
                                               war_room_trackers_trackers_reorder(war_room_id, body.get('ids'))))


@war_rooms_trackers_blueprint.post('/apply-template')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'],
         summary='Apply a tracker template: merge its steps by name and create its trackers. '
                 'Returns every step of the room and the created trackers')
def apply_tracker_template(war_room_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    template_id = _body().get('template_id')
    if not isinstance(template_id, int) or isinstance(template_id, bool):
        return response_api_error('template_id must be an integer')

    def _apply():
        steps, trackers = tracker_templates_apply(war_room_id, template_id, iris_current_user.id)
        if trackers:
            names = ', '.join(tracker.name for tracker in trackers)
            _emit_tracker_event(war_room_id, f'Applied a tracker template: {names}',
                                trackers[0].id if len(trackers) == 1 else None)
        return {'steps': [war_room_trackers_serialize_step(s) for s in steps],
                'trackers': _serialize_trackers(war_room_id, trackers)}
    return _handle(_apply)


@war_rooms_trackers_blueprint.post('/save-as-template')
@ac_api_requires(Permissions.server_administrator)
@api_doc(response_shape='created', tags=['WarRoomTrackers'],
         summary='Save the steps and trackers (all, or tracker_ids) of the war room as a new tracker template')
def save_trackers_as_template(war_room_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    body = _body()
    return _handle(lambda: tracker_templates_save_from_war_room(war_room_id, body, iris_current_user.id),
                   created=True)


@war_rooms_trackers_blueprint.get('/<int:tracker_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='Get a tracker')
def get_tracker(war_room_id, tracker_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return _handle(lambda: _serialize_tracker(war_room_id, war_room_trackers_get(war_room_id, tracker_id)))


@war_rooms_trackers_blueprint.patch('/<int:tracker_id>')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'], summary='Update a tracker (fields left out keep their value, the source cannot change)')
def update_tracker(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()

    def _update():
        previous = war_room_trackers_get(war_room_id, tracker_id).name
        tracker = war_room_trackers_update(war_room_id, tracker_id, body)
        if tracker.name != previous:
            _emit_tracker_event(war_room_id, f'Renamed tracker: {previous} → {tracker.name}', tracker.id)
        else:
            _emit_tracker_event(war_room_id, f'Updated tracker: {tracker.name}', tracker.id)
        return _serialize_tracker(war_room_id, tracker)
    return _handle(_update)


@war_rooms_trackers_blueprint.delete('/<int:tracker_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomTrackers'], summary='Delete a tracker with its rows and their history')
def delete_tracker(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err

    def _delete():
        name = war_room_trackers_delete(war_room_id, tracker_id)
        _emit_tracker_event(war_room_id, f'Deleted tracker: {name}')
    return _handle_deleted(_delete)


# ------------------------------------------------------------------ Rows --

@war_rooms_trackers_blueprint.get('/<int:tracker_id>/rows')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'],
         summary='List the rows of a tracker: every current source item merged with its state for a mirror, '
                 'the stored rows for a manual tracker (capped)')
def list_tracker_rows(war_room_id, tracker_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err
    return _handle(lambda: war_room_trackers_rows_list(war_room_id, war_room_trackers_get(war_room_id, tracker_id),
                                                       war_room_tracker_access(war_room_id)))


@war_rooms_trackers_blueprint.post('/<int:tracker_id>/rows')
@ac_api_requires()
@api_doc(response_shape='created', tags=['WarRoomTrackers'],
         summary='Add rows to a manual tracker (free text or war-room items; items already present are skipped unless '
                 'allow_duplicates)')
def add_tracker_rows(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()

    def _add():
        tracker = war_room_trackers_get(war_room_id, tracker_id)
        result = war_room_trackers_rows_add(war_room_id, tracker, body, war_room_tracker_access(war_room_id, writing=True),
                                            iris_current_user.id)
        if result['data']:
            rows = war_room_trackers_describe_rows((row['item_type'], row['label']) for row in result['data'])
            _emit_tracker_event(war_room_id, f'{tracker.name}: added {rows}', tracker.id)
        return result
    return _handle(_add, created=True)


@war_rooms_trackers_blueprint.post('/<int:tracker_id>/rows/state')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'],
         summary='Bulk update the steps, note, column values or label (custom label of an item row) of tracker '
                 'rows (one history event per change)')
def update_tracker_rows_state(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()

    def _update():
        tracker = war_room_trackers_get(war_room_id, tracker_id)
        result, changes = war_room_trackers_rows_update_state(war_room_id, tracker, body,
                                                              war_room_tracker_access(war_room_id, writing=True), iris_current_user.id)
        summary = war_room_trackers_describe_changes(changes)
        if summary is not None:
            _emit_tracker_event(war_room_id, f'{tracker.name}: {summary}', tracker.id)
        return result
    return _handle(_update)


@war_rooms_trackers_blueprint.post('/<int:tracker_id>/timeline/sync')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'],
         summary='Catch the timelines up with a tracker whose timeline sync was turned on after the fact: the '
                 'current state of the rows the caller sees (state mode), or the past changes of those rows not in '
                 'the timelines yet, oldest first and at most 2000 per call, `remaining` telling what is left and '
                 '`remaining_restricted` what is left for the members who see the other rows (changes mode). '
                 'Case timelines are only written where the caller has full access')
def sync_tracker_timeline(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err

    def _sync():
        tracker = war_room_trackers_get(war_room_id, tracker_id)
        summary = war_room_trackers_timeline_sync(war_room_id, tracker, war_room_tracker_access(war_room_id, writing=True),
                                                  iris_current_user.id)
        if summary['events']:
            what = 'its past changes' if 'remaining' in summary else 'its state'
            _emit_tracker_event(war_room_id, f'{tracker.name}: wrote {what} to the timelines', tracker.id)
        return summary
    return _handle(_sync)


@war_rooms_trackers_blueprint.post('/<int:tracker_id>/rows/reorder')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomTrackers'],
         summary='Reorder the rows of a manual tracker (listed ids first, the others after)')
def reorder_tracker_rows(war_room_id, tracker_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err
    body = _body()
    return _handle_deleted(lambda: war_room_trackers_rows_reorder(war_room_trackers_get(war_room_id, tracker_id),
                                                                  body.get('ids')))


@war_rooms_trackers_blueprint.get('/<int:tracker_id>/rows/history')
@ac_api_requires()
@api_doc(tags=['WarRoomTrackers'],
         summary='History of one row (row_id, or item_type and item_id) or the latest events of the tracker',
         query_params=[('row_id', 'integer', 'Events of this row'),
                       ('item_type', 'string', 'Events of the row of this item (with item_id)'),
                       ('item_id', 'integer', 'Events of the row of this item (with item_type)')])
def tracker_rows_history(war_room_id, tracker_id):
    err = require_war_room_read(war_room_id)
    if err is not None:
        return err

    def _history():
        return war_room_trackers_history(
            war_room_id, war_room_trackers_get(war_room_id, tracker_id), war_room_tracker_access(war_room_id),
            row_id=_query_int('row_id'), item_type=request.args.get('item_type') or None,
            item_id=_query_int('item_id'),
        )
    return _handle(_history)


@war_rooms_trackers_blueprint.delete('/<int:tracker_id>/rows/<int:row_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['WarRoomTrackers'], summary='Delete a row of a manual tracker with its history')
def delete_tracker_row(war_room_id, tracker_id, row_id):
    err = require_war_room_write(war_room_id)
    if err is not None:
        return err

    def _delete():
        tracker = war_room_trackers_get(war_room_id, tracker_id)
        row = war_room_trackers_rows_delete(tracker, row_id)
        _emit_tracker_event(war_room_id, f'{tracker.name}: removed {war_room_trackers_describe_rows([row])}',
                            tracker.id)
    return _handle_deleted(_delete)
