#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Aggregate queries behind the war-room board.

Every helper is a single grouped query over the given case ids so the
board renders in a constant number of round-trips whatever the number
of attached cases or assets.
"""

from sqlalchemy import and_
from sqlalchemy import case as sa_case
from sqlalchemy import func

from app.db import db
from app.models.alerts import Severity
from app.models.assets import AssetFlag
from app.models.assets import CaseAssetFlag
from app.models.assets import CaseAssets
from app.models.assets import CompromiseStatus
from app.models.authorization import User
from app.models.cases import Cases
from app.models.cases import CaseState
from app.models.customers import Client
from app.models.models import CaseTasks
from app.models.models import TaskStatus
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomDecision
from app.models.war_rooms import WarRoomDecisionApprover
from app.models.war_rooms import WarRoomTask


_CLOSED_TASK_STATUSES = ('done', 'closed', 'canceled', 'cancelled')
_OPEN_DECISION_STATUSES = ('proposed', 'approved')


def war_room_board_db_attached_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(WarRoomCase.attached_at.asc())
        .all()
    )
    return [r.case_id for r in rows]


def war_room_board_db_flags():
    return AssetFlag.query.order_by(AssetFlag.sort_order.asc(), AssetFlag.id.asc()).all()


def war_room_board_db_case_rows(case_ids):
    """Case header rows (customer, state, owner, severity, open tasks)."""
    if not case_ids:
        return []
    open_tasks_sq = (
        db.session.query(
            CaseTasks.task_case_id.label('case_id'),
            func.count(CaseTasks.id).label('tasks_open'),
        )
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(CaseTasks.task_case_id.in_(list(case_ids)))
        .filter(func.coalesce(func.lower(TaskStatus.status_name), '').notin_(_CLOSED_TASK_STATUSES))
        .group_by(CaseTasks.task_case_id)
        .subquery()
    )
    return (
        db.session.query(
            Cases.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
            Cases.state_id,
            CaseState.state_name,
            Cases.owner_id,
            User.name.label('owner_name'),
            Severity.severity_name,
            func.coalesce(open_tasks_sq.c.tasks_open, 0).label('tasks_open'),
        )
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .outerjoin(CaseState, CaseState.state_id == Cases.state_id)
        .outerjoin(User, User.id == Cases.owner_id)
        .outerjoin(Severity, Severity.severity_id == Cases.severity_id)
        .outerjoin(open_tasks_sq, open_tasks_sq.c.case_id == Cases.case_id)
        .filter(Cases.case_id.in_(list(case_ids)))
        .all()
    )


def war_room_board_db_asset_counts(case_ids):
    """Rows `(case_id, total, compromised, flagged)` per case; `flagged`
    counts the assets carrying at least one flag."""
    if not case_ids:
        return []
    compromised = sa_case(
        (CaseAssets.asset_compromise_status_id == CompromiseStatus.compromised.value, 1),
        else_=0,
    )
    flagged = sa_case((_has_flag(), 1), else_=0)
    return (
        db.session.query(
            CaseAssets.case_id,
            func.count(CaseAssets.asset_id).label('total'),
            func.coalesce(func.sum(compromised), 0).label('compromised'),
            func.coalesce(func.sum(flagged), 0).label('flagged'),
        )
        .filter(CaseAssets.case_id.in_(list(case_ids)))
        .group_by(CaseAssets.case_id)
        .all()
    )


def war_room_board_db_flag_counts(case_ids):
    """Rows `(case_id, flag_id, total)`: assets carrying each flag, per case."""
    if not case_ids:
        return []
    return (
        db.session.query(
            CaseAssetFlag.case_id,
            CaseAssetFlag.flag_id,
            func.count(CaseAssetFlag.asset_id).label('total'),
        )
        .filter(CaseAssetFlag.case_id.in_(list(case_ids)))
        .group_by(CaseAssetFlag.case_id, CaseAssetFlag.flag_id)
        .all()
    )


def war_room_board_db_kind_counts(case_ids):
    """Rows `(case_id, kind, total)`: assets carrying at least one flag of
    each kind, per case (an asset may count under several kinds)."""
    if not case_ids:
        return []
    return (
        db.session.query(
            CaseAssetFlag.case_id,
            AssetFlag.kind,
            func.count(func.distinct(CaseAssetFlag.asset_id)).label('total'),
        )
        .join(AssetFlag, AssetFlag.id == CaseAssetFlag.flag_id)
        .filter(CaseAssetFlag.case_id.in_(list(case_ids)))
        .group_by(CaseAssetFlag.case_id, AssetFlag.kind)
        .all()
    )


def _has_flag():
    return (
        db.session.query(CaseAssetFlag.asset_id)
        .filter(CaseAssetFlag.asset_id == CaseAssets.asset_id)
        .exists()
    )


def war_room_board_db_compromised_unflagged(case_ids, limit):
    """Compromised assets that carry no flag at all."""
    if not case_ids:
        return []
    return (
        db.session.query(CaseAssets.asset_id, CaseAssets.asset_name, CaseAssets.case_id)
        .filter(
            CaseAssets.case_id.in_(list(case_ids)),
            CaseAssets.asset_compromise_status_id == CompromiseStatus.compromised.value,
            ~_has_flag(),
        )
        .order_by(CaseAssets.case_id.asc(), CaseAssets.asset_id.asc())
        .limit(limit)
        .all()
    )


def war_room_board_db_exceptions_without_decision(case_ids, limit):
    """One row per (asset, exception flag) not backed by a decision."""
    if not case_ids:
        return []
    return (
        db.session.query(
            CaseAssets.asset_id,
            CaseAssets.asset_name,
            CaseAssets.case_id,
            AssetFlag.name.label('flag_name'),
        )
        .join(CaseAssetFlag, CaseAssetFlag.asset_id == CaseAssets.asset_id)
        .join(AssetFlag, AssetFlag.id == CaseAssetFlag.flag_id)
        .filter(
            CaseAssets.case_id.in_(list(case_ids)),
            AssetFlag.kind == 'exception',
            CaseAssetFlag.decision_id.is_(None),
        )
        .order_by(CaseAssets.case_id.asc(), CaseAssets.asset_id.asc(), AssetFlag.sort_order.asc())
        .limit(limit)
        .all()
    )


def war_room_board_db_open_decisions(war_room_id):
    """Open decisions (proposed/approved, not implemented) with the count
    of approvers who have not voted yet."""
    pending_sq = (
        db.session.query(
            WarRoomDecisionApprover.decision_id,
            func.count(WarRoomDecisionApprover.user_id).label('pending'),
        )
        .filter(WarRoomDecisionApprover.verdict.is_(None))
        .group_by(WarRoomDecisionApprover.decision_id)
        .subquery()
    )
    return (
        db.session.query(
            WarRoomDecision.decision_id,
            WarRoomDecision.number,
            WarRoomDecision.title,
            WarRoomDecision.status,
            WarRoomDecision.target_at,
            func.coalesce(pending_sq.c.pending, 0).label('pending_approvers'),
        )
        .outerjoin(pending_sq, pending_sq.c.decision_id == WarRoomDecision.decision_id)
        .filter(and_(
            WarRoomDecision.war_room_id == war_room_id,
            WarRoomDecision.status.in_(_OPEN_DECISION_STATUSES),
            WarRoomDecision.implemented_at.is_(None),
        ))
        .order_by(WarRoomDecision.number.asc())
        .all()
    )


def war_room_board_db_open_war_room_tasks_count(war_room_id):
    return (
        db.session.query(func.count(WarRoomTask.task_id))
        .filter(WarRoomTask.war_room_id == war_room_id, WarRoomTask.closed_at.is_(None))
        .scalar()
    ) or 0
