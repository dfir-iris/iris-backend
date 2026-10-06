#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Persistence helpers for the war-room task fan-out.

A war-room task can be pushed into each attached case as a regular
case task; `WarRoomTaskCaseLink` keeps track of which case task was
created from which war-room task. Query builders live here so the
business layer stays free of direct sqlalchemy imports.
"""

from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.authorization import User
from app.models.cases import Cases
from app.models.models import CaseTasks
from app.models.models import TaskAssignee
from app.models.models import TaskStatus
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomTask
from app.models.war_rooms import WarRoomTaskCaseLink


def fan_out_db_attached_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .all()
    )
    return {row.case_id for row in rows}


def fan_out_db_task_statuses():
    """All task statuses as `(id, status_name)` tuples, lowest id first."""
    rows = (
        db.session.query(TaskStatus.id, TaskStatus.status_name)
        .order_by(TaskStatus.id.asc())
        .all()
    )
    return [(row.id, row.status_name) for row in rows]


def fan_out_db_case_owner_ids(case_ids):
    if not case_ids:
        return {}
    rows = (
        db.session.query(Cases.case_id, Cases.owner_id)
        .filter(Cases.case_id.in_(list(case_ids)))
        .all()
    )
    return {row.case_id: row.owner_id for row in rows}


def fan_out_db_linked_case_ids(task_id):
    rows = (
        db.session.query(WarRoomTaskCaseLink.case_id)
        .filter(WarRoomTaskCaseLink.task_id == task_id)
        .all()
    )
    return {row.case_id for row in rows}


def fan_out_db_room_linked_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomTaskCaseLink.case_id)
        .join(WarRoomTask, WarRoomTask.task_id == WarRoomTaskCaseLink.task_id)
        .filter(WarRoomTask.war_room_id == war_room_id)
        .distinct()
        .all()
    )
    return {row.case_id for row in rows}


def fan_out_db_create_link(task_id, case_id, case_task_id, created_by_id):
    """Insert the link row; returns None when it already exists (race)."""
    link = WarRoomTaskCaseLink()
    link.task_id = task_id
    link.case_id = case_id
    link.case_task_id = case_task_id
    link.created_by_id = created_by_id
    db.session.add(link)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return link


def fan_out_db_delete_link(task_id, case_id):
    deleted = (
        WarRoomTaskCaseLink.query
        .filter(WarRoomTaskCaseLink.task_id == task_id,
                WarRoomTaskCaseLink.case_id == case_id)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    return deleted


def fan_out_db_link_rows(task_id):
    """Links of a task joined with the case task, its status and case."""
    return (
        db.session.query(
            WarRoomTaskCaseLink.case_id,
            WarRoomTaskCaseLink.case_task_id,
            WarRoomTaskCaseLink.created_at,
            Cases.name.label('case_name'),
            CaseTasks.task_title,
            CaseTasks.task_status_id.label('status_id'),
            TaskStatus.status_name,
        )
        .outerjoin(Cases, Cases.case_id == WarRoomTaskCaseLink.case_id)
        .outerjoin(CaseTasks, CaseTasks.id == WarRoomTaskCaseLink.case_task_id)
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(WarRoomTaskCaseLink.task_id == task_id)
        .order_by(WarRoomTaskCaseLink.created_at.asc(), WarRoomTaskCaseLink.id.asc())
        .all()
    )


def fan_out_db_assignees(case_task_ids):
    """`{case_task_id: [{id, name}]}` for the given case tasks."""
    out = {}
    if not case_task_ids:
        return out
    rows = (
        db.session.query(TaskAssignee.task_id, User.id, User.name)
        .join(User, User.id == TaskAssignee.user_id)
        .filter(TaskAssignee.task_id.in_(list(case_task_ids)))
        .order_by(User.name.asc())
        .all()
    )
    for row in rows:
        out.setdefault(row.task_id, []).append({'id': row.id, 'name': row.name})
    return out


def fan_out_db_summary_rows(war_room_id):
    """`(task_id, case_id, status_name)` for every link of the room."""
    return (
        db.session.query(
            WarRoomTaskCaseLink.task_id,
            WarRoomTaskCaseLink.case_id,
            TaskStatus.status_name,
        )
        .join(WarRoomTask, WarRoomTask.task_id == WarRoomTaskCaseLink.task_id)
        .outerjoin(CaseTasks, CaseTasks.id == WarRoomTaskCaseLink.case_task_id)
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(WarRoomTask.war_room_id == war_room_id)
        .all()
    )
