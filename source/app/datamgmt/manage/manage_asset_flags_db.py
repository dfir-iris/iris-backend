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

"""Query helpers for the `asset_flag` taxonomy, the flags set on case
assets (`case_asset_flag`) and their history (`case_asset_flag_history`)."""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.assets import AssetFlag
from app.models.assets import CaseAssetFlag
from app.models.assets import CaseAssetFlagHistory
from app.models.authorization import User
from app.models.cases import CaseEventTimeline
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.models import CaseEventsAssets
from app.models.war_rooms import WarRoom
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomDecision


def asset_flags_db_list():
    return AssetFlag.query.order_by(AssetFlag.sort_order.asc(), AssetFlag.id.asc()).all()


def asset_flags_db_get(flag_id):
    return AssetFlag.query.filter(AssetFlag.id == flag_id).first()


def asset_flags_db_find_by_name(name, exclude_id=None):
    """Case-insensitive lookup, used for the uniqueness check."""
    query = AssetFlag.query.filter(func.lower(AssetFlag.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(AssetFlag.id != exclude_id)
    return query.first()


def asset_flags_db_max_sort_order():
    value = db.session.query(func.max(AssetFlag.sort_order)).scalar()
    return value if value is not None else -1


def asset_flags_db_in_use_counts() -> dict:
    """{flag_id: number of case assets currently carrying that flag}."""
    rows = (
        db.session.query(CaseAssetFlag.flag_id, func.count(CaseAssetFlag.asset_id))
        .group_by(CaseAssetFlag.flag_id)
        .all()
    )
    return {flag_id: count for flag_id, count in rows}


def asset_flags_db_count_assets(flag_id) -> int:
    return db.session.query(func.count(CaseAssetFlag.asset_id)).filter(CaseAssetFlag.flag_id == flag_id).scalar() or 0


def asset_flags_db_save(flag) -> bool:
    """Add (if needed) and commit. False when a constraint (unique name) fails."""
    try:
        db.session.add(flag)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_flags_db_delete(flag) -> bool:
    """Delete and commit. False when the flag is still set on an asset."""
    try:
        db.session.delete(flag)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_flags_db_commit() -> bool:
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def asset_flags_db_rollback():
    db.session.rollback()


def asset_flags_db_replace_all(flags) -> bool:
    """Replace the whole taxonomy in one transaction.

    History rows keep their denormalised names: their flag FK is
    `ON DELETE SET NULL`. Returns False (and rolls back) when a flag
    became set on an asset in the meantime.
    """
    try:
        AssetFlag.query.delete(synchronize_session=False)
        db.session.add_all(flags)
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


def asset_flags_db_decision_war_room_id(decision_id):
    row = (
        db.session.query(WarRoomDecision.war_room_id)
        .filter(WarRoomDecision.decision_id == decision_id)
        .first()
    )
    return row[0] if row is not None else None


# ---- Flags of a case asset --------------------------------------------------

def asset_flags_db_get_asset_flag(asset_id, flag_id):
    return CaseAssetFlag.query.filter(CaseAssetFlag.asset_id == asset_id, CaseAssetFlag.flag_id == flag_id).first()


def asset_flags_db_add(obj):
    """Add to the session and flush, so generated ids are available.
    The caller commits."""
    db.session.add(obj)
    db.session.flush()


def asset_flags_db_remove(obj):
    db.session.delete(obj)
    db.session.flush()


def asset_flags_db_get_case_event(event_id, case_id):
    if event_id is None:
        return None
    return CasesEvent.query.filter(CasesEvent.event_id == event_id, CasesEvent.case_id == case_id).first()


def asset_flags_db_get_timeline(case_id, name):
    return CaseTimeline.query.filter(CaseTimeline.case_id == case_id, CaseTimeline.name == name).first()


def asset_flags_db_link_event(event_id, case_id, asset_id, timeline_id):
    """Link a freshly created event to its asset and timeline."""
    db.session.add(CaseEventsAssets(event_id=event_id, asset_id=asset_id, case_id=case_id))
    db.session.add(CaseEventTimeline(event_id=event_id, timeline_id=timeline_id))
    db.session.flush()


def asset_flags_db_history(asset_id):
    """History rows of an asset, newest first, with user / war room / decision labels."""
    return (
        db.session.query(
            CaseAssetFlagHistory,
            User.name.label('changed_by_name'),
            WarRoom.name.label('war_room_name'),
            WarRoomDecision.number.label('decision_number'),
        )
        .outerjoin(User, User.id == CaseAssetFlagHistory.changed_by_id)
        .outerjoin(WarRoom, WarRoom.war_room_id == CaseAssetFlagHistory.war_room_id)
        .outerjoin(WarRoomDecision, WarRoomDecision.decision_id == CaseAssetFlagHistory.decision_id)
        .filter(CaseAssetFlagHistory.asset_id == asset_id)
        .order_by(CaseAssetFlagHistory.changed_at.desc(), CaseAssetFlagHistory.id.desc())
        .all()
    )
