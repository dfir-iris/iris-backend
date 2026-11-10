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

"""SQLAlchemy models of the case trackers.

The case counterpart of the war-room trackers (`models/war_rooms.py`):
user-defined tabs of a case whose rows carry case steps, a note and
extra column values. A tracker is `manual` or mirrors the case assets,
IOCs, tasks or evidences. Both features share the tracker templates
(`TrackerTemplate`) but none of their tables.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from app.db import db


CASE_TRACKER_SOURCES = ('manual', 'assets', 'iocs', 'tasks', 'evidences')

CASE_TRACKER_ITEM_TYPES = ('asset', 'ioc', 'task', 'evidence')


class CaseTrackerStep(db.Model):
    """Step a row of any tracker of the case can carry (several at once).
    Names are unique per case, case-insensitively."""
    __tablename__ = 'case_tracker_step'
    __table_args__ = (
        Index('uq_case_tracker_step_name', 'case_id', text('lower(name)'), unique=True),
    )

    id = Column(BigInteger, primary_key=True)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(16), nullable=False, server_default=text("'slate'"))
    icon = Column(String(64), nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseTracker(db.Model):
    """User-defined tab of a case.

    `source='manual'` rows are added by hand; any other source mirrors
    the case (assets, IOCs, tasks, evidences): its rows are computed on
    read and only their state is stored, lazily, in `CaseTrackerRow`.
    `step_ids` restricts the steps offered (NULL = every step of the
    case), `columns` holds the extra columns. `timeline_sync` (NULL =
    off) sends the row changes or states to a case timeline, see
    `business/case_tracker_timelines.py`."""
    __tablename__ = 'case_tracker'
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'assets', 'iocs', 'tasks', 'evidences')",
                        name='ck_case_tracker_source'),
    )

    id = Column(BigInteger, primary_key=True)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    icon = Column(String(64), nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(32), nullable=False)
    step_ids = Column(JSONB, nullable=True)
    columns = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    show_asset_flags = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    timeline_sync = Column(JSONB, nullable=True)
    sort_order = Column(Integer, nullable=False, server_default=text('0'))
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseTrackerRow(db.Model):
    """A manual row, or the stored state of a mirrored item.

    Item rows point at `(item_type, item_id)`, with an optional custom
    `label`; free-text rows have no item and a `label`. A manual tracker
    can hold several rows of one item, a mirror at most one (the
    business layer locks the tracker before creating it)."""
    __tablename__ = 'case_tracker_row'
    __table_args__ = (
        Index('ix_case_tracker_row_item', 'tracker_id', 'item_type', 'item_id'),
    )

    id = Column(BigInteger, primary_key=True)
    tracker_id = Column(BigInteger, ForeignKey('case_tracker.id', ondelete='CASCADE'), nullable=False, index=True)
    item_type = Column(String(32), nullable=True)
    item_id = Column(BigInteger, nullable=True)
    label = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    values = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    sort_order = Column(Integer, nullable=False, server_default=text('0'))
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_at = Column(DateTime, nullable=True)


class CaseTrackerRowStep(db.Model):
    """A step currently set on a case tracker row."""
    __tablename__ = 'case_tracker_row_step'

    row_id = Column(BigInteger, ForeignKey('case_tracker_row.id', ondelete='CASCADE'),
                    primary_key=True, nullable=False)
    step_id = Column(BigInteger, ForeignKey('case_tracker_step.id', ondelete='CASCADE'),
                     primary_key=True, nullable=False, index=True)
    set_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    set_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseTrackerRowEvent(db.Model):
    """One change of a case tracker row (who, when, what). `action` is one
    of created, step_added, step_removed, note_changed, value_changed,
    label_changed. `in_timeline`: handled by a `changes` timeline sync,
    live or by the backfill (`case_trackers_timeline_sync`)."""
    __tablename__ = 'case_tracker_row_event'

    id = Column(BigInteger, primary_key=True)
    tracker_id = Column(BigInteger, ForeignKey('case_tracker.id', ondelete='CASCADE'), nullable=False, index=True)
    row_id = Column(BigInteger, ForeignKey('case_tracker_row.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    action = Column(String(32), nullable=False)
    details = Column(JSONB, nullable=True)
    in_timeline = Column(Boolean, nullable=False, default=False, server_default=text('false'))


class CaseTrackerRowTimelineEvent(db.Model):
    """The case timeline event holding the state of a tracker row (`state`
    sync mode). An event deleted from the timeline leaves its id NULL: the
    next change writes a new one."""
    __tablename__ = 'case_tracker_row_timeline_event'

    id = Column(BigInteger, primary_key=True)
    row_id = Column(BigInteger, ForeignKey('case_tracker_row.id', ondelete='CASCADE'),
                    nullable=False, unique=True)
    case_event_id = Column(BigInteger, ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True)
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))
