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

"""Query helpers for the `asset_stage` taxonomy and the stage history of
case assets (`case_asset_stage_history`)."""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.assets import AssetStage
from app.models.assets import CaseAssetStageHistory
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomDecision


def asset_stages_db_list():
    return AssetStage.query.order_by(AssetStage.sort_order.asc(), AssetStage.id.asc()).all()


def asset_stages_db_get(stage_id):
    return AssetStage.query.filter(AssetStage.id == stage_id).first()


def asset_stages_db_find_by_name(name, exclude_id=None):
    """Case-insensitive lookup, used for the uniqueness check."""
    query = AssetStage.query.filter(func.lower(AssetStage.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(AssetStage.id != exclude_id)
    return query.first()


def asset_stages_db_max_sort_order():
    value = db.session.query(func.max(AssetStage.sort_order)).scalar()
    return value if value is not None else -1


def asset_stages_db_in_use_counts() -> dict:
    """{stage_id: number of case assets currently in that stage}."""
    rows = (
        db.session.query(CaseAssets.stage_id, func.count(CaseAssets.asset_id))
        .filter(CaseAssets.stage_id.isnot(None))
        .group_by(CaseAssets.stage_id)
        .all()
    )
    return {stage_id: count for stage_id, count in rows}


def asset_stages_db_count_assets(stage_id) -> int:
    return db.session.query(func.count(CaseAssets.asset_id)).filter(CaseAssets.stage_id == stage_id).scalar() or 0


def asset_stages_db_save(stage) -> bool:
    """Add (if needed) and commit. False when a constraint (unique name) fails."""
    try:
        db.session.add(stage)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_stages_db_delete(stage) -> bool:
    """Delete and commit. False when the stage is still referenced."""
    try:
        db.session.delete(stage)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_stages_db_commit() -> bool:
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_stages_db_replace_all(stages) -> bool:
    """Replace the whole taxonomy in one transaction.

    History rows keep their denormalised names: their stage FKs are
    `ON DELETE SET NULL`. Returns False (and rolls back) when a stage
    became referenced by an asset in the meantime.
    """
    try:
        AssetStage.query.delete(synchronize_session=False)
        db.session.add_all(stages)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def decision_belongs_to_case_war_room(decision_id, case_id):
    """The decision when it belongs to a war room `case_id` is attached to, else None."""
    return (
        WarRoomDecision.query
        .join(WarRoomCase, WarRoomCase.war_room_id == WarRoomDecision.war_room_id)
        .filter(WarRoomDecision.decision_id == decision_id, WarRoomCase.case_id == case_id)
        .first()
    )


def asset_stages_db_decision_war_room_id(decision_id):
    row = (
        db.session.query(WarRoomDecision.war_room_id)
        .filter(WarRoomDecision.decision_id == decision_id)
        .first()
    )
    return row[0] if row is not None else None


def asset_stages_db_add_history(entry):
    db.session.add(entry)


def asset_stages_db_rollback():
    db.session.rollback()


def asset_stages_db_history(asset_id):
    """History rows of an asset, newest first, with user / war room / decision labels."""
    return (
        db.session.query(
            CaseAssetStageHistory,
            User.name.label('changed_by_name'),
            WarRoom.name.label('war_room_name'),
            WarRoomDecision.number.label('decision_number'),
        )
        .outerjoin(User, User.id == CaseAssetStageHistory.changed_by_id)
        .outerjoin(WarRoom, WarRoom.war_room_id == CaseAssetStageHistory.war_room_id)
        .outerjoin(WarRoomDecision, WarRoomDecision.decision_id == CaseAssetStageHistory.decision_id)
        .filter(CaseAssetStageHistory.asset_id == asset_id)
        .order_by(CaseAssetStageHistory.changed_at.desc(), CaseAssetStageHistory.id.desc())
        .all()
    )
