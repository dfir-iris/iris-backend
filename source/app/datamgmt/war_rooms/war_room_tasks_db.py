#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Persistence helpers for war-room tasks.

Query builders live here so the business layer stays free of direct
sqlalchemy imports (import-linter contract). Behavioural notes are on
the business-layer callers.
"""

from sqlalchemy import func, or_
from sqlalchemy.orm import aliased

from app.db import db
from app.models.war_rooms import WarRoomTask


_SUBTASKS_SUPPORTED = None


def subtasks_supported():
    global _SUBTASKS_SUPPORTED
    if _SUBTASKS_SUPPORTED is True:
        return True
    try:
        from sqlalchemy import text as _text
        with db.engine.connect() as conn:
            conn.execute(
                _text('SELECT parent_task_id FROM war_room_task LIMIT 0')
            )
        supported = True
    except Exception as e:
        from app.logger import logger
        pgcode = getattr(getattr(e, 'orig', None), 'pgcode', None)
        if pgcode == '42703':
            logger.info('Subtasks disabled: parent_task_id column missing')
        else:
            logger.exception(
                'Subtasks support probe failed unexpectedly (pgcode=%s)',
                pgcode,
            )
        return False
    if supported:
        _SUBTASKS_SUPPORTED = True
    return supported


def base_task_query(war_room_id):
    from app.models.authorization import User
    from app.models.models import TaskStatus

    Assignee = aliased(User)
    Creator = aliased(User)
    Closer = aliased(User)

    columns = [
        WarRoomTask.task_id,
        WarRoomTask.war_room_id,
        WarRoomTask.title,
        WarRoomTask.description,
        WarRoomTask.status_id,
        WarRoomTask.assignee_id,
        WarRoomTask.due_at,
        WarRoomTask.source_case_id,
        WarRoomTask.source_case_task_id,
        WarRoomTask.created_at,
        WarRoomTask.created_by_id,
        WarRoomTask.closed_at,
        WarRoomTask.closed_by_id,
        WarRoomTask.tags,
        Assignee.user.label('assignee_login'),
        Assignee.name.label('assignee_name'),
        Creator.user.label('created_by_login'),
        Creator.name.label('created_by_name'),
        Closer.user.label('closed_by_login'),
        Closer.name.label('closed_by_name'),
        TaskStatus.status_name.label('status_name'),
        TaskStatus.status_bscolor.label('status_bscolor'),
    ]
    if subtasks_supported():
        columns.append(WarRoomTask.parent_task_id.label('parent_task_id'))

    q = (
        db.session.query(*columns)
        .outerjoin(Assignee, Assignee.id == WarRoomTask.assignee_id)
        .outerjoin(Creator, Creator.id == WarRoomTask.created_by_id)
        .outerjoin(Closer, Closer.id == WarRoomTask.closed_by_id)
        .outerjoin(TaskStatus, TaskStatus.id == WarRoomTask.status_id)
        .filter(WarRoomTask.war_room_id == war_room_id)
    )
    return q


def apply_search_filter(query, needle_lower):
    needle = f'%{needle_lower}%'
    return query.filter(or_(
        func.lower(WarRoomTask.title).like(needle),
        func.lower(func.coalesce(WarRoomTask.description, '')).like(needle),
    ))


def apply_assignee_filter(query, assignee_ids):
    conds = []
    real_ids = [aid for aid in assignee_ids if aid and aid != 0]
    if 0 in assignee_ids or None in assignee_ids:
        conds.append(WarRoomTask.assignee_id.is_(None))
    if real_ids:
        conds.append(WarRoomTask.assignee_id.in_(real_ids))
    if conds:
        return query.filter(or_(*conds))
    return query


def apply_tag_filter(query, tags):
    tag_conds = []
    tag_expr = func.lower(
        func.concat(',', func.coalesce(WarRoomTask.tags, ''), ',')
    )
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            continue
        needle = f'%,{t.strip().lower()},%'
        tag_conds.append(tag_expr.like(needle))
    if tag_conds:
        return query.filter(or_(*tag_conds))
    return query


def apply_due_range_filter(query, due_from, due_to, include_no_due):
    range_conds = []
    if due_from is not None and due_to is not None:
        range_conds.append(WarRoomTask.due_at.between(due_from, due_to))
    elif due_from is not None:
        range_conds.append(WarRoomTask.due_at >= due_from)
    else:
        range_conds.append(WarRoomTask.due_at <= due_to)
    if include_no_due:
        range_conds.append(WarRoomTask.due_at.is_(None))
    return query.filter(or_(*range_conds))
