#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Persistence helpers for war rooms.

Query builders live here so the business layer stays free of direct
sqlalchemy imports (import-linter contract).
"""

from sqlalchemy import case as sa_case, func, or_

from app.db import db
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomCase


def state_priority_expr(state_sort_order):
    """SQLAlchemy CASE that maps the state string to a sort integer.

    Anything unrecognised falls to the end so a future state added
    without updating this table still sorts predictably.
    """
    return sa_case(
        state_sort_order,
        value=WarRoom.state,
        else_=len(state_sort_order),
    )


def apply_search_filter(query, needle):
    return query.filter(or_(
        WarRoom.name.ilike(needle),
        WarRoom.description.ilike(needle),
    ))


def war_room_case_attachments_rows(war_room_id):
    from app.models.authorization import User
    from app.models.cases import Cases
    from app.models.cases import CaseState
    from app.models.customers import Client
    from app.models.models import CaseTasks, TaskStatus

    open_status_clause = func.lower(TaskStatus.status_name).notin_(
        ['done', 'closed', 'cancelled']
    )

    task_total_sq = (
        db.session.query(
            CaseTasks.task_case_id.label('case_id'),
            func.count(CaseTasks.id).label('task_count'),
            func.sum(
                sa_case((open_status_clause, 1), else_=0)
            ).label('task_open_count'),
        )
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .group_by(CaseTasks.task_case_id)
        .subquery()
    )

    rows = (
        db.session.query(
            WarRoomCase.war_room_id,
            WarRoomCase.case_id,
            WarRoomCase.attached_at,
            WarRoomCase.note,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
            Cases.owner_id,
            User.name.label('owner_name'),
            User.user.label('owner_login'),
            Cases.open_date,
            Cases.close_date,
            Cases.state_id,
            CaseState.state_name,
            func.coalesce(task_total_sq.c.task_count, 0).label('task_count'),
            func.coalesce(task_total_sq.c.task_open_count, 0).label(
                'task_open_count'
            ),
        )
        .join(Cases, Cases.case_id == WarRoomCase.case_id)
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .outerjoin(User, User.id == Cases.owner_id)
        .outerjoin(CaseState, CaseState.state_id == Cases.state_id)
        .outerjoin(
            task_total_sq, task_total_sq.c.case_id == WarRoomCase.case_id
        )
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(WarRoomCase.attached_at.asc())
        .all()
    )
    return rows
