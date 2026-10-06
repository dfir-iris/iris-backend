#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Fan-out of a war-room task into the attached cases.

Each target case receives a regular case task (created through
`tasks_create` so modules hooks, activity and case state keep firing)
and a `WarRoomTaskCaseLink` row ties it back to the war-room task.
Authorization (war-room write, full access on each case) is the
caller blueprint's job; this module only re-checks that the targets
are attached to the room.
"""

from app.business.tasks import tasks_create
from app.business.tasks import tasks_delete
from app.business.war_room_tasks import war_room_task_get
from app.business.war_rooms import war_room_get
from app.datamgmt.manage.manage_tags_db import add_db_tag
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_assignees
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_attached_case_ids
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_case_owner_ids
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_create_link
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_delete_link
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_link_rows
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_linked_case_ids
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_room_linked_case_ids
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_summary_rows
from app.datamgmt.war_rooms.war_room_fan_out_db import fan_out_db_task_statuses
from app.db import db
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.logger import logger
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.models import CaseTasks


_DONE_STATUS_NAMES = ('done', 'closed', 'canceled', 'cancelled')
_DEFAULT_STATUS_NAME = 'to do'
_FAN_OUT_TAG = 'war-room'
_MAX_TAG_LEN = 64


def war_room_task_fan_out_is_done_status(status_name):
    """True when a case-task status name means "finished"."""
    if not isinstance(status_name, str):
        return False
    return status_name.strip().lower() in _DONE_STATUS_NAMES


def _resolve_default_status_id(statuses):
    """'To do' (case-insensitive) when present, else the lowest id."""
    if not statuses:
        return None
    for status_id, name in statuses:
        if isinstance(name, str) and name.strip().lower() == _DEFAULT_STATUS_NAME:
            return status_id
    return min(status_id for status_id, _ in statuses)


def _resolve_status_id(status_id):
    statuses = fan_out_db_task_statuses()
    if status_id is None:
        resolved = _resolve_default_status_id(statuses)
        if resolved is None:
            raise BusinessProcessingError('No task status is configured')
        return resolved
    if status_id not in {sid for sid, _ in statuses}:
        raise BusinessProcessingError('Unknown status_id')
    return status_id


def _build_tags(task_tags):
    """`war-room` first, then the war-room task tags, de-duplicated."""
    out = [_FAN_OUT_TAG]
    seen = {_FAN_OUT_TAG}
    for raw in (task_tags or '').split(','):
        tag = raw.strip()[:_MAX_TAG_LEN]
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    return out


def _build_description(war_room_name, task):
    back_ref = f'From war room {war_room_name} (task #{task.task_id})'
    description = (task.description or '').strip()
    if description:
        return f'{description}\n\n---\n{back_ref}'
    return back_ref


def _register_tags(tags):
    for tag in tags:
        try:
            add_db_tag(tag)
        except Exception:
            db.session.rollback()
            logger.exception(f'Unable to register tag "{tag}"')


def _create_one(task, case_id, description, tags_csv, status_id, assignee_ids, user_id):
    case_task = CaseTasks()
    case_task.task_title = task.title
    case_task.task_description = description
    case_task.task_tags = tags_csv
    case_task.task_status_id = status_id
    case_task.task_case_id = case_id
    case_task = tasks_create(case_task, assignee_ids)
    link = fan_out_db_create_link(task.task_id, case_id, case_task.id, user_id)
    if link is None:
        # Concurrent fan-out won the race: drop our duplicate case task.
        tasks_delete(case_task)
        return {'case_id': case_id, 'status': 'exists'}
    return {'case_id': case_id, 'status': 'created', 'case_task_id': case_task.id}


def war_room_task_fan_out_create(war_room_id, task_id, case_ids, user_id,
                                 assign_to_case_owner=True, status_id=None):
    """Create one case task per target case.

    `case_ids` must already be authorized by the caller. Returns
    `[{case_id, status, case_task_id?, message?}]` in input order.
    """
    task = war_room_task_get(war_room_id, task_id)
    war_room = war_room_get(war_room_id)
    resolved_status_id = _resolve_status_id(status_id)

    attached = fan_out_db_attached_case_ids(war_room_id)
    already_linked = fan_out_db_linked_case_ids(task_id)
    owners = fan_out_db_case_owner_ids(case_ids) if assign_to_case_owner else {}
    tags = _build_tags(task.tags)
    _register_tags(tags)
    tags_csv = ','.join(tags)
    description = _build_description(war_room.name, task)

    results = []
    created = 0
    for case_id in case_ids:
        if case_id not in attached:
            results.append({'case_id': case_id, 'status': 'denied',
                            'message': 'Case is not attached to this war room'})
            continue
        if case_id in already_linked:
            results.append({'case_id': case_id, 'status': 'exists'})
            continue
        owner_id = owners.get(case_id)
        assignee_ids = [owner_id] if owner_id else []
        try:
            result = _create_one(task, case_id, description, tags_csv,
                                 resolved_status_id, assignee_ids, user_id)
        except BusinessProcessingError as e:
            db.session.rollback()
            result = {'case_id': case_id, 'status': 'error', 'message': e.get_message()}
        except Exception:
            db.session.rollback()
            logger.exception(f'Fan-out of war room task #{task_id} into case #{case_id} failed')
            result = {'case_id': case_id, 'status': 'error',
                      'message': 'Unable to create the case task'}
        if result['status'] == 'created':
            created += 1
        results.append(result)

    if created:
        track_activity(f'fanned out war room task "{task.title}" to {created} case(s)',
                       war_room_id=war_room_id)
        call_modules_hook('on_postload_war_room_task_fan_out_create',
                          {'war_room_id': war_room_id, 'task_id': task_id, 'title': task.title,
                           'results': results})
    return results


def war_room_task_fan_out_linked_case_ids(war_room_id, task_id):
    war_room_task_get(war_room_id, task_id)
    return fan_out_db_linked_case_ids(task_id)


def war_room_task_fan_out_room_case_ids(war_room_id):
    return fan_out_db_room_linked_case_ids(war_room_id)


def _iso(value):
    return value.isoformat() if value else None


def war_room_task_fan_out_status(war_room_id, task_id, readable_case_ids):
    """Per-case status of a fanned-out task.

    Cases outside `readable_case_ids` only surface their id with
    `accessible: False`.
    """
    war_room_task_get(war_room_id, task_id)
    rows = fan_out_db_link_rows(task_id)
    readable_task_ids = [row.case_task_id for row in rows if row.case_id in readable_case_ids]
    assignees = fan_out_db_assignees(readable_task_ids)
    out = []
    for row in rows:
        if row.case_id not in readable_case_ids:
            out.append({'case_id': row.case_id, 'accessible': False,
                        'created_at': _iso(row.created_at)})
            continue
        out.append({
            'case_id': row.case_id,
            'accessible': True,
            'case_name': row.case_name,
            'case_task_id': row.case_task_id,
            'task_title': row.task_title,
            'status_id': row.status_id,
            'status_name': row.status_name,
            'done': war_room_task_fan_out_is_done_status(row.status_name),
            'assignees': assignees.get(row.case_task_id, []),
            'created_at': _iso(row.created_at),
        })
    return out


def war_room_task_fan_out_summarize(rows, readable_case_ids):
    """Aggregate `(task_id, case_id, status_name)` rows per task.

    `done` only counts cases the caller can read so the status of an
    unreadable case task never leaks.
    """
    summary = {}
    for task_id, case_id, status_name in rows:
        entry = summary.setdefault(str(task_id), {'total': 0, 'done': 0, 'accessible_total': 0})
        entry['total'] += 1
        if case_id in readable_case_ids:
            entry['accessible_total'] += 1
            if war_room_task_fan_out_is_done_status(status_name):
                entry['done'] += 1
    return summary


def war_room_task_fan_out_summary(war_room_id, readable_case_ids):
    rows = [(row.task_id, row.case_id, row.status_name)
            for row in fan_out_db_summary_rows(war_room_id)]
    return war_room_task_fan_out_summarize(rows, readable_case_ids)


def war_room_task_fan_out_unlink(war_room_id, task_id, case_id):
    task = war_room_task_get(war_room_id, task_id)
    if not fan_out_db_delete_link(task_id, case_id):
        raise ObjectNotFoundError()
    track_activity(f'unlinked war room task "{task.title}" from case #{case_id}',
                   war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_task_fan_out_delete',
                      {'war_room_id': war_room_id, 'task_id': task_id, 'title': task.title, 'case_id': case_id})
