#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Read queries feeding the SitRep auto-draft and cadence.

Every case-scoped query takes an explicit `case_ids` list: the caller
passes only the cases the current user can read, so nothing from an
unreadable case reaches the draft.
"""

from sqlalchemy import case as sa_case
from sqlalchemy import func

from app.db import db
from app.models.assets import AssetStage
from app.models.assets import CaseAssetStageHistory
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import Cases
from app.models.cases import CaseState
from app.models.customers import Client
from app.models.models import CaseTasks
from app.models.models import TaskStatus
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomDecision
from app.models.war_rooms import WarRoomSitRep
from app.models.war_rooms import WarRoomTask


_CLOSED_TASK_STATUS_NAMES = ['done', 'closed', 'canceled', 'cancelled']


def sitreps_db_attached_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(WarRoomCase.attached_at.asc())
        .all()
    )
    return [row.case_id for row in rows]


def sitreps_db_last_published_at(war_room_id):
    return (
        db.session.query(func.max(WarRoomSitRep.authored_at))
        .filter(WarRoomSitRep.war_room_id == war_room_id,
                WarRoomSitRep.published.is_(True))
        .scalar()
    )


def sitreps_db_stage_transitions(case_ids, since, limit):
    if not case_ids:
        return []
    query = (
        db.session.query(
            CaseAssetStageHistory.case_id,
            CaseAssetStageHistory.asset_id,
            CaseAssets.asset_name,
            Cases.name.label('case_name'),
            CaseAssetStageHistory.from_stage_name,
            CaseAssetStageHistory.to_stage_name,
            CaseAssetStageHistory.reason,
            CaseAssetStageHistory.changed_at,
            User.name.label('changed_by_name'),
        )
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetStageHistory.asset_id)
        .join(Cases, Cases.case_id == CaseAssetStageHistory.case_id)
        .outerjoin(User, User.id == CaseAssetStageHistory.changed_by_id)
        .filter(CaseAssetStageHistory.case_id.in_(list(case_ids)))
    )
    if since is not None:
        query = query.filter(CaseAssetStageHistory.changed_at > since)
    return (
        query
        .order_by(CaseAssetStageHistory.changed_at.desc(), CaseAssetStageHistory.id.desc())
        .limit(limit)
        .all()
    )


def sitreps_db_decisions(war_room_id):
    return (
        db.session.query(
            WarRoomDecision.decision_id,
            WarRoomDecision.number,
            WarRoomDecision.title,
            WarRoomDecision.status,
            WarRoomDecision.target_at,
            WarRoomDecision.created_at,
            WarRoomDecision.decided_at,
            WarRoomDecision.implemented_at,
            User.name.label('owner_name'),
        )
        .outerjoin(User, User.id == WarRoomDecision.owner_id)
        .filter(WarRoomDecision.war_room_id == war_room_id)
        .order_by(WarRoomDecision.number.asc())
        .all()
    )


def sitreps_db_cases_attached_since(war_room_id, case_ids, since):
    if not case_ids:
        return []
    query = (
        db.session.query(WarRoomCase.case_id, WarRoomCase.attached_at,
                         Cases.name.label('case_name'))
        .join(Cases, Cases.case_id == WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id,
                WarRoomCase.case_id.in_(list(case_ids)))
    )
    if since is not None:
        query = query.filter(WarRoomCase.attached_at > since)
    return query.order_by(WarRoomCase.attached_at.asc()).all()


def sitreps_db_case_rows(war_room_id, case_ids):
    """Per-case figures: name, customer, state, assets, done, exceptions, open tasks."""
    if not case_ids:
        return []
    asset_sq = (
        db.session.query(
            CaseAssets.case_id.label('case_id'),
            func.count(CaseAssets.asset_id).label('assets_total'),
            func.sum(sa_case((AssetStage.kind == 'done', 1), else_=0)).label('assets_done'),
            func.sum(sa_case((AssetStage.kind == 'exception', 1), else_=0)).label('assets_exception'),
        )
        .outerjoin(AssetStage, AssetStage.id == CaseAssets.stage_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)))
        .group_by(CaseAssets.case_id)
        .subquery()
    )
    open_clause = func.lower(func.coalesce(TaskStatus.status_name, '')).notin_(
        _CLOSED_TASK_STATUS_NAMES
    )
    task_sq = (
        db.session.query(
            CaseTasks.task_case_id.label('case_id'),
            func.sum(sa_case((open_clause, 1), else_=0)).label('tasks_open'),
        )
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(CaseTasks.task_case_id.in_(list(case_ids)))
        .group_by(CaseTasks.task_case_id)
        .subquery()
    )
    return (
        db.session.query(
            Cases.case_id,
            Cases.name.label('case_name'),
            Client.name.label('customer_name'),
            CaseState.state_name,
            func.coalesce(asset_sq.c.assets_total, 0).label('assets_total'),
            func.coalesce(asset_sq.c.assets_done, 0).label('assets_done'),
            func.coalesce(asset_sq.c.assets_exception, 0).label('assets_exception'),
            func.coalesce(task_sq.c.tasks_open, 0).label('tasks_open'),
        )
        .join(WarRoomCase, WarRoomCase.case_id == Cases.case_id)
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .outerjoin(CaseState, CaseState.state_id == Cases.state_id)
        .outerjoin(asset_sq, asset_sq.c.case_id == Cases.case_id)
        .outerjoin(task_sq, task_sq.c.case_id == Cases.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id,
                Cases.case_id.in_(list(case_ids)))
        .order_by(WarRoomCase.attached_at.asc())
        .all()
    )


def sitreps_db_exception_assets(case_ids, limit, war_room_id=None):
    """Assets in an exception stage. The linked decision ref is only
    resolved for decisions of `war_room_id` (never another room's)."""
    if not case_ids:
        return []
    return (
        db.session.query(
            CaseAssets.asset_id,
            CaseAssets.asset_name,
            CaseAssets.case_id,
            Cases.name.label('case_name'),
            AssetStage.name.label('stage_name'),
            CaseAssets.stage_reason,
            WarRoomDecision.number.label('decision_number'),
        )
        .join(AssetStage, AssetStage.id == CaseAssets.stage_id)
        .join(Cases, Cases.case_id == CaseAssets.case_id)
        .outerjoin(WarRoomDecision,
                   (WarRoomDecision.decision_id == CaseAssets.stage_decision_id)
                   & (WarRoomDecision.war_room_id == war_room_id))
        .filter(CaseAssets.case_id.in_(list(case_ids)),
                AssetStage.kind == 'exception')
        .order_by(Cases.case_id.asc(), CaseAssets.asset_name.asc())
        .limit(limit)
        .all()
    )


def sitreps_db_open_tasks(war_room_id, limit):
    return (
        db.session.query(
            WarRoomTask.task_id,
            WarRoomTask.title,
            WarRoomTask.due_at,
            User.name.label('assignee_name'),
        )
        .outerjoin(User, User.id == WarRoomTask.assignee_id)
        .filter(WarRoomTask.war_room_id == war_room_id,
                WarRoomTask.closed_at.is_(None))
        .order_by(WarRoomTask.due_at.asc().nulls_last(), WarRoomTask.task_id.asc())
        .limit(limit)
        .all()
    )
