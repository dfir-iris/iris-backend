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

"""Persistence helpers for the case trackers and the timeline events they
write. Behavioural notes are on the business-layer callers in
`business/case_trackers.py` and `business/case_tracker_timelines.py`.
Timeline writes are flushed, never committed: the caller commits with
the tracker change."""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models.authorization import User
from app.models.case_trackers import CaseTracker
from app.models.case_trackers import CaseTrackerRow
from app.models.case_trackers import CaseTrackerRowEvent
from app.models.case_trackers import CaseTrackerRowStep
from app.models.case_trackers import CaseTrackerRowTimelineEvent
from app.models.case_trackers import CaseTrackerStep
from app.models.cases import CaseEventTimeline
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.evidences import CaseReceivedFile
from app.models.evidences import EvidenceTypes
from app.models.models import CaseEventsAssets
from app.models.models import CaseEventsIoc
from app.models.models import CaseTasks
from app.models.models import TaskAssignee
from app.models.models import TaskStatus


# ------------------------------------------------------------- Session --

def case_trackers_db_add(obj):
    """Add and flush (the id is needed right away)."""
    db.session.add(obj)
    db.session.flush()
    return obj


def case_trackers_db_delete(obj):
    db.session.delete(obj)
    db.session.flush()


def case_trackers_db_commit():
    """Commit; False (rolled back) on a uniqueness race."""
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


# --------------------------------------------------------------- Steps --

def case_trackers_db_steps(case_id):
    return (
        CaseTrackerStep.query
        .filter(CaseTrackerStep.case_id == case_id)
        .order_by(CaseTrackerStep.sort_order.asc(), CaseTrackerStep.id.asc())
        .all()
    )


def case_trackers_db_step_get(case_id, step_id):
    return CaseTrackerStep.query.filter(
        CaseTrackerStep.case_id == case_id,
        CaseTrackerStep.id == step_id,
    ).first()


def case_trackers_db_step_find_by_name(case_id, name, exclude_id=None):
    query = CaseTrackerStep.query.filter(
        CaseTrackerStep.case_id == case_id,
        func.lower(CaseTrackerStep.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(CaseTrackerStep.id != exclude_id)
    return query.first()


# ------------------------------------------------------------ Trackers --

def case_trackers_db_trackers(case_id):
    return (
        CaseTracker.query
        .filter(CaseTracker.case_id == case_id)
        .order_by(CaseTracker.sort_order.asc(), CaseTracker.id.asc())
        .all()
    )


def case_trackers_db_tracker_get(case_id, tracker_id):
    return CaseTracker.query.filter(
        CaseTracker.case_id == case_id,
        CaseTracker.id == tracker_id,
    ).first()


def case_trackers_db_tracker_lock(tracker_id):
    """Lock a tracker until the commit: serialises the creation of the
    stored row of a mirrored item."""
    CaseTracker.query.filter(CaseTracker.id == tracker_id).with_for_update().first()


def case_trackers_db_row_counts(tracker_ids):
    """`{tracker_id: stored rows}`."""
    if not tracker_ids:
        return {}
    rows = (
        db.session.query(CaseTrackerRow.tracker_id, func.count(CaseTrackerRow.id).label('rows'))
        .filter(CaseTrackerRow.tracker_id.in_(list(tracker_ids)))
        .group_by(CaseTrackerRow.tracker_id)
        .all()
    )
    return {row.tracker_id: int(row.rows or 0) for row in rows}


# ---------------------------------------------------------------- Rows --

def case_trackers_db_rows(tracker_id, limit=None):
    """Stored rows of a tracker in display order (`limit + 1` rows at
    most when set, so the caller detects truncation)."""
    query = (
        CaseTrackerRow.query
        .filter(CaseTrackerRow.tracker_id == tracker_id)
        .order_by(CaseTrackerRow.sort_order.asc(), CaseTrackerRow.id.asc())
    )
    if limit is not None:
        query = query.limit(limit + 1)
    return query.all()


def case_trackers_db_rows_by_ids(tracker_id, row_ids):
    if not row_ids:
        return []
    return CaseTrackerRow.query.filter(
        CaseTrackerRow.tracker_id == tracker_id,
        CaseTrackerRow.id.in_(list(row_ids)),
    ).all()


def case_trackers_db_rows_max_sort(tracker_id):
    value = (
        db.session.query(func.max(CaseTrackerRow.sort_order))
        .filter(CaseTrackerRow.tracker_id == tracker_id)
        .scalar()
    )
    return value if value is not None else -1


def case_trackers_db_row_steps(row_ids):
    """Steps set on `row_ids`, as `(row_id, step_id, set_by_id, set_at)`."""
    if not row_ids:
        return []
    return (
        db.session.query(CaseTrackerRowStep.row_id, CaseTrackerRowStep.step_id,
                         CaseTrackerRowStep.set_by_id, CaseTrackerRowStep.set_at)
        .filter(CaseTrackerRowStep.row_id.in_(list(row_ids)))
        .order_by(CaseTrackerRowStep.row_id.asc(), CaseTrackerRowStep.set_at.asc(),
                  CaseTrackerRowStep.step_id.asc())
        .all()
    )


def case_trackers_db_row_step_remove(row_id, step_id):
    CaseTrackerRowStep.query.filter(
        CaseTrackerRowStep.row_id == row_id,
        CaseTrackerRowStep.step_id == step_id,
    ).delete(synchronize_session=False)


def case_trackers_db_rows_keys(row_ids):
    """`{row_id: (item_type, item_id)}`."""
    if not row_ids:
        return {}
    rows = (
        db.session.query(CaseTrackerRow.id, CaseTrackerRow.item_type, CaseTrackerRow.item_id)
        .filter(CaseTrackerRow.id.in_(list(row_ids)))
        .all()
    )
    return {row.id: (row.item_type, row.item_id) for row in rows}


# -------------------------------------------------------------- Events --

def case_trackers_db_events(tracker_id, row_ids=None, limit=200):
    """Newest first."""
    query = CaseTrackerRowEvent.query.filter(CaseTrackerRowEvent.tracker_id == tracker_id)
    if row_ids is not None:
        if not row_ids:
            return []
        query = query.filter(CaseTrackerRowEvent.row_id.in_(list(row_ids)))
    return (
        query
        .order_by(CaseTrackerRowEvent.created_at.desc(), CaseTrackerRowEvent.id.desc())
        .limit(limit)
        .all()
    )


def _not_in_timeline(query, tracker_id, row_ids):
    return query.filter(CaseTrackerRowEvent.tracker_id == tracker_id,
                        CaseTrackerRowEvent.row_id.in_(list(row_ids)),
                        CaseTrackerRowEvent.in_timeline.is_(False))


def case_trackers_db_events_not_in_timeline(tracker_id, row_ids, limit):
    """The oldest changes of these rows not written to the timeline,
    oldest first."""
    if not row_ids:
        return []
    return (
        _not_in_timeline(CaseTrackerRowEvent.query, tracker_id, row_ids)
        .order_by(CaseTrackerRowEvent.id)
        .limit(limit)
        .all()
    )


def case_trackers_db_events_not_in_timeline_count(tracker_id, row_ids):
    """How many changes of these rows are not written to the timeline."""
    if not row_ids:
        return 0
    return _not_in_timeline(db.session.query(func.count(CaseTrackerRowEvent.id)), tracker_id,
                            row_ids).scalar() or 0


def case_trackers_db_value_events_after(row_ids, after_id):
    """The value changes of these rows after the event `after_id`, oldest
    first."""
    if not row_ids:
        return []
    return (
        CaseTrackerRowEvent.query
        .filter(CaseTrackerRowEvent.row_id.in_(list(row_ids)), CaseTrackerRowEvent.id > after_id,
                CaseTrackerRowEvent.action == 'value_changed')
        .order_by(CaseTrackerRowEvent.id)
        .all()
    )


# ------------------------------------------------------------- Sources --

def case_trackers_db_tasks(case_id, limit):
    """The tasks of a case with their status, `limit + 1` at most."""
    return (
        db.session.query(CaseTasks.id, CaseTasks.task_title, CaseTasks.task_status_id,
                         TaskStatus.status_name, TaskStatus.status_bscolor, CaseTasks.task_open_date,
                         CaseTasks.task_close_date, CaseTasks.task_last_update)
        .outerjoin(TaskStatus, TaskStatus.id == CaseTasks.task_status_id)
        .filter(CaseTasks.task_case_id == case_id)
        .order_by(CaseTasks.id.asc())
        .limit(limit + 1)
        .all()
    )


def case_trackers_db_task_assignees(task_ids):
    """`{task_id: [(user_id, name)]}`."""
    if not task_ids:
        return {}
    rows = (
        db.session.query(TaskAssignee.task_id, User.id, User.name)
        .join(User, User.id == TaskAssignee.user_id)
        .filter(TaskAssignee.task_id.in_(list(task_ids)))
        .order_by(User.name.asc())
        .all()
    )
    out = {}
    for row in rows:
        out.setdefault(row.task_id, []).append((row.id, row.name))
    return out


def case_trackers_db_evidences(case_id, limit):
    """The evidences of a case with their type, `limit + 1` at most."""
    return (
        db.session.query(CaseReceivedFile.id, CaseReceivedFile.filename, CaseReceivedFile.file_size,
                         CaseReceivedFile.file_hash, CaseReceivedFile.date_added,
                         CaseReceivedFile.acquisition_date, EvidenceTypes.name.label('type_name'))
        .outerjoin(EvidenceTypes, EvidenceTypes.id == CaseReceivedFile.type_id)
        .filter(CaseReceivedFile.case_id == case_id)
        .order_by(CaseReceivedFile.id.asc())
        .limit(limit + 1)
        .all()
    )


# ------------------------------------------------------------ Timeline --

def case_trackers_db_case_timeline(case_id, timeline_id):
    return CaseTimeline.query.filter(CaseTimeline.case_id == case_id,
                                     CaseTimeline.timeline_id == timeline_id).first()


def case_trackers_db_case_timeline_by_name(case_id, name, build):
    """The case timeline named `name`, created with `build()` when missing.
    A concurrent creation (unique name) is caught in a savepoint and the
    winner returned. Returns `(timeline, created)`."""
    def query():
        return CaseTimeline.query.filter(CaseTimeline.case_id == case_id, CaseTimeline.name == name)

    timeline = query().first()
    if timeline is not None:
        return timeline, False
    try:
        with db.session.begin_nested():
            timeline = build()
            db.session.add(timeline)
    except IntegrityError:
        return query().first(), False
    return timeline, True


def case_trackers_db_link_case_event(event_id, case_id, timeline_id, asset_ids, ioc_ids):
    db.session.add(CaseEventTimeline(event_id=event_id, timeline_id=timeline_id))
    for asset_id in asset_ids:
        db.session.add(CaseEventsAssets(event_id=event_id, asset_id=asset_id, case_id=case_id))
    for ioc_id in ioc_ids:
        db.session.add(CaseEventsIoc(event_id=event_id, ioc_id=ioc_id, case_id=case_id))
    db.session.flush()


def case_trackers_db_case_event(case_id, event_id):
    return CasesEvent.query.filter(CasesEvent.case_id == case_id, CasesEvent.event_id == event_id).first()


def case_trackers_db_state_links(row_ids):
    """`{row_id: link}` of the state events of the rows."""
    if not row_ids:
        return {}
    links = CaseTrackerRowTimelineEvent.query.filter(CaseTrackerRowTimelineEvent.row_id.in_(list(row_ids))).all()
    return {link.row_id: link for link in links}
