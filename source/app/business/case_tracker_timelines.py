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

"""Timeline events written by case trackers.

A tracker with a `timeline_sync` setting writes its rows to a timeline of
its case, with the same `mode` and `date` settings as the war-room
trackers (see `war_room_tracker_timelines`): `changes` writes one event
per row and per update, `state` keeps one event per row updated in place.
The timeline is `case_timeline_id`, or a "Trackers" timeline created on
the first event. Asset and IOC rows link their event to the asset / IOC.

Nothing is committed here: the events are flushed in the transaction of
the tracker change, which the caller commits. `finish()` logs the case
activity once committed.
"""

from app.business.war_room_tracker_timelines import WAR_ROOM_TRACKER_TIMELINE_MODES
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_changes_text
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_event_date
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_state_text
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_validate
from app.datamgmt.case.case_trackers_db import case_trackers_db_add
from app.datamgmt.case.case_trackers_db import case_trackers_db_case_event
from app.datamgmt.case.case_trackers_db import case_trackers_db_case_timeline
from app.datamgmt.case.case_trackers_db import case_trackers_db_case_timeline_by_name
from app.datamgmt.case.case_trackers_db import case_trackers_db_link_case_event
from app.datamgmt.case.case_trackers_db import case_trackers_db_state_links
from app.datamgmt.states import update_timeline_state
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_user_names
from app.iris_engine.utils.tracker import track_activity
from app.models.case_trackers import CaseTrackerRowTimelineEvent
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.errors import BusinessProcessingError
from app.util import add_obj_history_entry


CASE_TRACKER_TIMELINE_NAME = 'Trackers'

_EVENT_SOURCE = 'IRIS case tracker'
_EVENT_TAGS = 'case-tracker'
_TIMELINE_COLOR = '#0ea5e9'
_TIMELINE_DESCRIPTION = 'Events written by the trackers of this case.'


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


# ----------------------------------------------------------- Validation --

def case_tracker_timelines_validate(value, columns, case_id=None):
    """The normalised `timeline_sync` of a case tracker (`{mode, date,
    case_timeline_id}`), None to turn it off. `case_timeline_id` must be a
    timeline of the case; it is dropped without a case (templates). A
    `target` (war-room setting) is ignored."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError('timeline_sync must be an object or null')
    if value.get('mode') not in WAR_ROOM_TRACKER_TIMELINE_MODES:
        raise BusinessProcessingError(
            f'timeline_sync.mode must be one of: {", ".join(WAR_ROOM_TRACKER_TIMELINE_MODES)}')
    sync = war_room_tracker_timelines_validate({'mode': value['mode'], 'date': value.get('date'), 'target': 'cases'},
                                               columns)
    timeline_id = value.get('case_timeline_id') if case_id is not None else None
    if timeline_id is not None:
        if not _is_int(timeline_id) or timeline_id <= 0:
            raise BusinessProcessingError('timeline_sync.case_timeline_id must be an integer')
        if case_trackers_db_case_timeline(case_id, timeline_id) is None:
            raise BusinessProcessingError(f'Timeline #{timeline_id} is not a timeline of this case')
    return {'mode': sync['mode'], 'date': sync['date'], 'case_timeline_id': timeline_id}


def case_tracker_timelines_item_links(asset_ids=(), ioc_ids=()):
    """What a source item brings to its timeline events: the assets and
    IOCs they link to."""
    return {'asset_ids': tuple(asset_ids), 'ioc_ids': tuple(ioc_ids)}


# --------------------------------------------------------------- Writer --

class CaseTrackerTimelineWriter:
    """Writes the timeline events of the rows of one tracker during one
    request. Disabled (every call a no-op) when the tracker has no
    `timeline_sync`."""

    def __init__(self, case_id, tracker, user_id, now, step_names):
        self._case_id = case_id
        self._tracker = tracker
        self._sync = tracker.timeline_sync
        self._user_id = user_id
        self._now = now
        self._step_names = step_names
        self._names = {}
        self._timeline = None
        self._created_timeline = False
        self._links = {}
        self._events = 0

    @property
    def enabled(self):
        return self._sync is not None

    @property
    def mode(self):
        return self._sync.get('mode') if self._sync else None

    def prefetch(self, row_ids):
        """Load the state events of these rows in one query."""
        if self.mode != 'state':
            return
        row_ids = [row_id for row_id in row_ids if row_id not in self._links]
        found = case_trackers_db_state_links(row_ids)
        for row_id in row_ids:
            self._links[row_id] = found.get(row_id)

    def row_changed(self, row, item, events, step_ids):
        """Write the events of a row changed by `events` (`(action,
        details)`), whose steps are now `step_ids`."""
        if not self.enabled or not events:
            return
        if self.mode == 'changes':
            self._write(row, item, events=events, author_id=self._user_id)
        else:
            self._write(row, item, step_ids=step_ids)

    def row_history(self, row, item, events, changed_at, values, author_id):
        """Write past `events` of a row (`changes` mode): made by
        `author_id` at `changed_at`, when the row's values were `values`."""
        if self.mode == 'changes' and events:
            self._write(row, item, events=events, values=values, changed_at=changed_at, author_id=author_id)

    def row_state(self, row, item, step_ids):
        """Write (or refresh) the state event of a row."""
        if self.mode == 'state':
            self._write(row, item, step_ids=step_ids)

    def summary(self):
        """None when off, else `{events}`: the events written."""
        if not self.enabled:
            return None
        return {'events': self._events}

    def finish(self):
        """Log the case activity, once the events are committed."""
        if self._created_timeline:
            track_activity(f'created timeline "{CASE_TRACKER_TIMELINE_NAME}"', caseid=self._case_id)
        if self._events:
            track_activity(f'tracker "{self._tracker.name}" updated the timeline "{self._timeline.name}"',
                           caseid=self._case_id)

    # -- internals

    def _user_names(self, values, events, author_id=None):
        user_keys = {column['key'] for column in self._tracker.columns or () if column.get('type') == 'user'}
        user_ids = {value for key, value in (values or {}).items() if key in user_keys and _is_int(value)}
        if _is_int(author_id):
            user_ids.add(author_id)
        for action, details in events or ():
            if action == 'value_changed' and details.get('column_key') in user_keys:
                user_ids.update(details.get(key) for key in ('old', 'new') if _is_int(details.get(key)))
        user_ids -= set(self._names)
        if user_ids:
            self._names.update(war_room_trackers_db_user_names(user_ids))
        return self._names

    def _write(self, row, item, events=None, step_ids=None, values=None, changed_at=None, author_id=None):
        if row.item_type is not None and item is None:
            return
        tracker = self._tracker
        if row.item_type is None:
            label = row.label
        elif row.label:
            label = f"{row.label} ({item['label']})"
        else:
            label = item['label']
        if values is None:
            values = row.values
        if events is None:
            author_id = row.updated_by_id or row.created_by_id
        names = self._user_names(values, events, author_id)
        author = (names.get(author_id) or f'user #{author_id}') if _is_int(author_id) else None
        if events is not None:
            title, content = war_room_tracker_timelines_changes_text(tracker.name, label, events, tracker.columns,
                                                                     names, author=author)
        else:
            step_labels = [name for step_id, name in self._step_names.items() if step_id in set(step_ids)]
            title, content = war_room_tracker_timelines_state_text(tracker.name, label, step_labels, tracker.columns,
                                                                   values, row.note, names, updated_by=author)
        event_date = war_room_tracker_timelines_event_date(self._sync, tracker.columns, values,
                                                           changed_at or self._now)
        links = (item or {}).get('timeline') or case_tracker_timelines_item_links()
        self._case_event(row, links, title, content, event_date)

    def _case_timeline(self):
        if self._timeline is None:
            timeline_id = self._sync.get('case_timeline_id')
            if timeline_id is not None:
                self._timeline = case_trackers_db_case_timeline(self._case_id, timeline_id)
            if self._timeline is None:
                self._timeline, self._created_timeline = case_trackers_db_case_timeline_by_name(
                    self._case_id, CASE_TRACKER_TIMELINE_NAME,
                    lambda: CaseTimeline(case_id=self._case_id, name=CASE_TRACKER_TIMELINE_NAME,
                                         description=_TIMELINE_DESCRIPTION, color=_TIMELINE_COLOR,
                                         is_default=False, created_by_id=self._user_id))
        return self._timeline

    def _case_event(self, row, links, title, content, event_date):
        if self.mode == 'state' and row.id not in self._links:
            self.prefetch([row.id])
        link = self._links.get(row.id) if self.mode == 'state' else None
        event = None
        if link is not None and link.case_event_id is not None:
            event = case_trackers_db_case_event(self._case_id, link.case_event_id)
        timeline = self._case_timeline()
        if event is not None:
            event.event_title = title
            event.event_content = content
            event.event_date = event_date
            event.event_date_wtz = event_date
            event.event_tz = '+00:00'
            add_obj_history_entry(event, 'updated')
        else:
            event = CasesEvent(
                case_id=self._case_id,
                event_title=title,
                event_source=_EVENT_SOURCE,
                event_content=content,
                event_raw='',
                event_date=event_date,
                event_date_wtz=event_date,
                event_tz='+00:00',
                event_added=self._now,
                event_in_graph=True,
                event_in_summary=False,
                event_is_flagged=False,
                event_color='',
                event_tags=_EVENT_TAGS,
                user_id=self._user_id,
            )
            add_obj_history_entry(event, 'created')
            case_trackers_db_add(event)
            case_trackers_db_link_case_event(event.event_id, self._case_id, timeline.timeline_id,
                                             links['asset_ids'], links['ioc_ids'])
        if self.mode == 'state':
            if link is None:
                self._links[row.id] = case_trackers_db_add(CaseTrackerRowTimelineEvent(
                    row_id=row.id, case_event_id=event.event_id, updated_at=self._now))
            else:
                link.case_event_id = event.event_id
                link.updated_at = self._now
        if not self._events:
            update_timeline_state(self._case_id, self._user_id)
        self._events += 1
