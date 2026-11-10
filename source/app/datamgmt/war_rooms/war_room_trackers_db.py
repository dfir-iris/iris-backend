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

"""Persistence helpers for the war-room trackers and the tracker
templates. Behavioural notes are on the business-layer callers in
`business/war_room_trackers.py` and `business/war_room_tracker_templates.py`.
"""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.assets import AssetFlag
from app.models.authorization import User
from app.models.war_rooms import TrackerTemplate
from app.models.war_rooms import WarRoomTracker
from app.models.war_rooms import WarRoomTrackerRow
from app.models.war_rooms import WarRoomTrackerRowEvent
from app.models.war_rooms import WarRoomTrackerRowStep
from app.models.war_rooms import WarRoomTrackerStep


# ------------------------------------------------------------- Session --

def war_room_trackers_db_add(obj):
    """Add and flush (the id is needed right away)."""
    db.session.add(obj)
    db.session.flush()
    return obj


def war_room_trackers_db_delete(obj):
    db.session.delete(obj)
    db.session.flush()


def war_room_trackers_db_commit():
    """Commit; False (rolled back) on a uniqueness race."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def war_room_trackers_db_rollback():
    db.session.rollback()


# --------------------------------------------------------------- Steps --

def war_room_trackers_db_steps(war_room_id):
    return (
        WarRoomTrackerStep.query
        .filter(WarRoomTrackerStep.war_room_id == war_room_id)
        .order_by(WarRoomTrackerStep.sort_order.asc(), WarRoomTrackerStep.id.asc())
        .all()
    )


def war_room_trackers_db_step_get(war_room_id, step_id):
    return WarRoomTrackerStep.query.filter(
        WarRoomTrackerStep.war_room_id == war_room_id,
        WarRoomTrackerStep.id == step_id,
    ).first()


def war_room_trackers_db_step_find_by_name(war_room_id, name, exclude_id=None):
    query = WarRoomTrackerStep.query.filter(
        WarRoomTrackerStep.war_room_id == war_room_id,
        func.lower(WarRoomTrackerStep.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(WarRoomTrackerStep.id != exclude_id)
    return query.first()


# ------------------------------------------------------------ Trackers --

def war_room_trackers_db_trackers(war_room_id):
    return (
        WarRoomTracker.query
        .filter(WarRoomTracker.war_room_id == war_room_id)
        .order_by(WarRoomTracker.sort_order.asc(), WarRoomTracker.id.asc())
        .all()
    )


def war_room_trackers_db_tracker_get(war_room_id, tracker_id):
    return WarRoomTracker.query.filter(
        WarRoomTracker.war_room_id == war_room_id,
        WarRoomTracker.id == tracker_id,
    ).first()


def war_room_trackers_db_tracker_lock(tracker_id):
    """Lock a tracker until the commit: serialises the creation of the
    stored row of a mirrored item."""
    WarRoomTracker.query.filter(WarRoomTracker.id == tracker_id).with_for_update().first()


def war_room_trackers_db_trackers_by_ids(war_room_id, tracker_ids):
    if not tracker_ids:
        return []
    return (
        WarRoomTracker.query
        .filter(WarRoomTracker.war_room_id == war_room_id, WarRoomTracker.id.in_(list(tracker_ids)))
        .order_by(WarRoomTracker.sort_order.asc(), WarRoomTracker.id.asc())
        .all()
    )


def war_room_trackers_db_row_counts(tracker_ids):
    """`{tracker_id: stored rows}`."""
    if not tracker_ids:
        return {}
    rows = (
        db.session.query(WarRoomTrackerRow.tracker_id, func.count(WarRoomTrackerRow.id).label('rows'))
        .filter(WarRoomTrackerRow.tracker_id.in_(list(tracker_ids)))
        .group_by(WarRoomTrackerRow.tracker_id)
        .all()
    )
    return {row.tracker_id: int(row.rows or 0) for row in rows}


# ---------------------------------------------------------------- Rows --

def war_room_trackers_db_rows(tracker_id, limit=None):
    """Stored rows of a tracker in display order (`limit + 1` rows at
    most when set, so the caller detects truncation)."""
    query = (
        WarRoomTrackerRow.query
        .filter(WarRoomTrackerRow.tracker_id == tracker_id)
        .order_by(WarRoomTrackerRow.sort_order.asc(), WarRoomTrackerRow.id.asc())
    )
    if limit is not None:
        query = query.limit(limit + 1)
    return query.all()


def war_room_trackers_db_rows_by_ids(tracker_id, row_ids):
    if not row_ids:
        return []
    return WarRoomTrackerRow.query.filter(
        WarRoomTrackerRow.tracker_id == tracker_id,
        WarRoomTrackerRow.id.in_(list(row_ids)),
    ).all()


def war_room_trackers_db_rows_max_sort(tracker_id):
    value = (
        db.session.query(func.max(WarRoomTrackerRow.sort_order))
        .filter(WarRoomTrackerRow.tracker_id == tracker_id)
        .scalar()
    )
    return value if value is not None else -1


def war_room_trackers_db_row_steps(row_ids):
    """Steps set on `row_ids`, as `(row_id, step_id, set_by_id, set_at)`."""
    if not row_ids:
        return []
    return (
        db.session.query(WarRoomTrackerRowStep.row_id, WarRoomTrackerRowStep.step_id,
                         WarRoomTrackerRowStep.set_by_id, WarRoomTrackerRowStep.set_at)
        .filter(WarRoomTrackerRowStep.row_id.in_(list(row_ids)))
        .order_by(WarRoomTrackerRowStep.row_id.asc(), WarRoomTrackerRowStep.set_at.asc(),
                  WarRoomTrackerRowStep.step_id.asc())
        .all()
    )


def war_room_trackers_db_row_step_remove(row_id, step_id):
    WarRoomTrackerRowStep.query.filter(
        WarRoomTrackerRowStep.row_id == row_id,
        WarRoomTrackerRowStep.step_id == step_id,
    ).delete(synchronize_session=False)


def war_room_trackers_db_events(tracker_id, row_ids=None, limit=200):
    """Newest first."""
    query = WarRoomTrackerRowEvent.query.filter(WarRoomTrackerRowEvent.tracker_id == tracker_id)
    if row_ids is not None:
        if not row_ids:
            return []
        query = query.filter(WarRoomTrackerRowEvent.row_id.in_(list(row_ids)))
    return (
        query
        .order_by(WarRoomTrackerRowEvent.created_at.desc(), WarRoomTrackerRowEvent.id.desc())
        .limit(limit)
        .all()
    )


def _not_in_timeline(query, tracker_id, row_ids):
    return query.filter(WarRoomTrackerRowEvent.tracker_id == tracker_id,
                        WarRoomTrackerRowEvent.row_id.in_(list(row_ids)),
                        WarRoomTrackerRowEvent.in_timeline.is_(False))


def war_room_trackers_db_events_not_in_timeline(tracker_id, row_ids, limit):
    """The oldest changes of these rows not written to the timelines,
    oldest first."""
    if not row_ids:
        return []
    return (
        _not_in_timeline(WarRoomTrackerRowEvent.query, tracker_id, row_ids)
        .order_by(WarRoomTrackerRowEvent.id)
        .limit(limit)
        .all()
    )


def war_room_trackers_db_events_not_in_timeline_count(tracker_id, row_ids):
    """How many changes of these rows are not written to the timelines."""
    if not row_ids:
        return 0
    return _not_in_timeline(db.session.query(func.count(WarRoomTrackerRowEvent.id)), tracker_id,
                            row_ids).scalar() or 0


def war_room_trackers_db_value_events_after(row_ids, after_id):
    """The value changes of these rows after the event `after_id`, oldest
    first."""
    if not row_ids:
        return []
    return (
        WarRoomTrackerRowEvent.query
        .filter(WarRoomTrackerRowEvent.row_id.in_(list(row_ids)), WarRoomTrackerRowEvent.id > after_id,
                WarRoomTrackerRowEvent.action == 'value_changed')
        .order_by(WarRoomTrackerRowEvent.id)
        .all()
    )


def war_room_trackers_db_events_not_in_timeline_counts(tracker_ids):
    """`{tracker_id: changes not written to the timelines}`."""
    if not tracker_ids:
        return {}
    rows = (
        db.session.query(WarRoomTrackerRowEvent.tracker_id, func.count(WarRoomTrackerRowEvent.id).label('events'))
        .filter(WarRoomTrackerRowEvent.tracker_id.in_(list(tracker_ids)),
                WarRoomTrackerRowEvent.in_timeline.is_(False))
        .group_by(WarRoomTrackerRowEvent.tracker_id)
        .all()
    )
    return {row.tracker_id: int(row.events or 0) for row in rows}


def war_room_trackers_db_rows_keys(row_ids):
    """`{row_id: (item_type, item_id)}`."""
    if not row_ids:
        return {}
    rows = (
        db.session.query(WarRoomTrackerRow.id, WarRoomTrackerRow.item_type, WarRoomTrackerRow.item_id)
        .filter(WarRoomTrackerRow.id.in_(list(row_ids)))
        .all()
    )
    return {row.id: (row.item_type, row.item_id) for row in rows}


# --------------------------------------------------------------- Users --

def war_room_trackers_db_user_names(user_ids):
    user_ids = [user_id for user_id in set(user_ids or ()) if user_id is not None]
    if not user_ids:
        return {}
    rows = db.session.query(User.id, User.name).filter(User.id.in_(user_ids)).all()
    return {row.id: row.name for row in rows}


def war_room_trackers_db_existing_user_ids(user_ids):
    if not user_ids:
        return set()
    rows = db.session.query(User.id).filter(User.id.in_(list(user_ids))).all()
    return {row.id for row in rows}


def war_room_trackers_db_asset_flags():
    """`{flag_id: (name, color, icon)}` of the asset flag taxonomy."""
    rows = db.session.query(AssetFlag.id, AssetFlag.name, AssetFlag.color, AssetFlag.icon).all()
    return {row.id: (row.name, row.color, row.icon) for row in rows}


# ----------------------------------------------------------- Templates --

def war_room_trackers_db_templates():
    return TrackerTemplate.query.order_by(func.lower(TrackerTemplate.name).asc(), TrackerTemplate.id.asc()).all()


def war_room_trackers_db_template_get(template_id):
    return TrackerTemplate.query.filter(TrackerTemplate.id == template_id).first()


def war_room_trackers_db_template_find_by_name(name, exclude_id=None):
    query = TrackerTemplate.query.filter(func.lower(TrackerTemplate.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(TrackerTemplate.id != exclude_id)
    return query.first()
