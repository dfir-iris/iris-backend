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

"""Timeline events written by war-room trackers.

A tracker with a `timeline_sync` setting writes its rows to timelines:

- `mode`: `changes` writes one event per row and per update, listing what
  changed; `state` keeps one event per row (and per timeline) holding the
  current steps, values and note, updated in place.
- `date`: `changed_at` dates the event at the change, otherwise it is the
  key of a `date` / `datetime` column of the tracker, whose value dates
  the event (the change time when the row has none).

Turned on after the fact, a `changes` sync can write the changes made
before (the tracker history entries not `in_timeline`, see
`war_room_trackers_timeline_sync`): each is dated at its own time, or by
the date column as it was right after it (`row_history`).
- `target`: `cases` writes to the "War-room trackers" timeline of the case
  of each item (every case of an IOC, registry asset or customer),
  `war_room` to a war-room timeline (`war_room_timeline_id`, or a
  "Trackers" timeline created on the first event). Free-text rows (and
  items without a case) always go to the war-room timeline, and are
  counted in the summary (`war_room_rows`) when the target is `cases`.

Permissions, decided by the blueprint and handed in through the
`WarRoomTrackerAccess`:

- an event is written in a case timeline only when the user has full
  access to the case (`writable_case_ids`); the other cases are skipped
  and reported back. Case events never name the war room.
- a war-room event about a case item carries the cases of the item
  (`visibility_case_ids`), or the registry asset (`visibility_managed_asset_id`):
  the timeline routes only show it to the members who can read them.
- rows whose item the user cannot see (`restricted`) are never written.
- an event is created by the user writing it, which may not be who made
  the change (a backfill, a state refresh): its content names the author
  of the changes (`changes`) or the last user who changed the row (`state`).

Nothing is committed here: the events are flushed in the transaction of
the tracker change, which the caller commits. `finish()` logs the case
activity once committed.
"""

import datetime

from app.datamgmt.states import update_timeline_state
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_add
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_case_event
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_case_timeline
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_link_case_event
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_state_links
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_war_room_event
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import war_room_tracker_timelines_db_war_room_timeline
from app.datamgmt.war_rooms.war_room_tracker_timelines_db import \
    war_room_tracker_timelines_db_war_room_timeline_by_name
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_user_names
from app.iris_engine.utils.tracker import track_activity
from app.models.cases import CaseTimeline
from app.models.cases import CasesEvent
from app.models.errors import BusinessProcessingError
from app.models.war_rooms import WarRoomTimeline
from app.models.war_rooms import WarRoomTimelineEvent
from app.models.war_rooms import WarRoomTrackerRowTimelineEvent
from app.util import add_obj_history_entry


WAR_ROOM_TRACKER_TIMELINE_MODES = ('changes', 'state')
WAR_ROOM_TRACKER_TIMELINE_TARGETS = ('cases', 'war_room')
WAR_ROOM_TRACKER_TIMELINE_CHANGED_AT = 'changed_at'
WAR_ROOM_TRACKER_CASE_TIMELINE_NAME = 'War-room trackers'
WAR_ROOM_TRACKER_ROOM_TIMELINE_NAME = 'Trackers'

_DATE_COLUMN_TYPES = ('date', 'datetime')
_EVENT_SOURCE = 'IRIS war-room tracker'
_EVENT_TAGS = 'war-room-tracker'
_TIMELINE_COLOR = '#0ea5e9'
_CASE_TIMELINE_DESCRIPTION = 'Events written by the war-room trackers following this case.'
_ROOM_TIMELINE_DESCRIPTION = 'Events written by the trackers of this war room.'
_TITLE_MAX = 255
_EMPTY = '—'


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


# ----------------------------------------------------------- Validation --

def _validate_date_key(value, columns):
    if value is None or value == WAR_ROOM_TRACKER_TIMELINE_CHANGED_AT:
        return WAR_ROOM_TRACKER_TIMELINE_CHANGED_AT
    column = next((column for column in columns or () if column.get('key') == value), None)
    if column is None or column.get('type') not in _DATE_COLUMN_TYPES:
        raise BusinessProcessingError('The timeline date must be "changed_at" or a date / datetime column '
                                      'of the tracker')
    return value


def war_room_tracker_timelines_validate(value, columns, war_room_id=None):
    """The normalised `timeline_sync` of a tracker, None to turn it off.
    `war_room_timeline_id` must be a timeline of the war room; it is
    dropped without a war room (tracker templates)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError('timeline_sync must be an object or null')
    mode = value.get('mode')
    if mode not in WAR_ROOM_TRACKER_TIMELINE_MODES:
        raise BusinessProcessingError(
            f'timeline_sync.mode must be one of: {", ".join(WAR_ROOM_TRACKER_TIMELINE_MODES)}')
    target = value.get('target')
    if target not in WAR_ROOM_TRACKER_TIMELINE_TARGETS:
        raise BusinessProcessingError(
            f'timeline_sync.target must be one of: {", ".join(WAR_ROOM_TRACKER_TIMELINE_TARGETS)}')
    timeline_id = value.get('war_room_timeline_id')
    if war_room_id is None:
        timeline_id = None
    elif timeline_id is not None:
        if not _is_int(timeline_id) or timeline_id <= 0:
            raise BusinessProcessingError('timeline_sync.war_room_timeline_id must be an integer')
        if war_room_tracker_timelines_db_war_room_timeline(war_room_id, timeline_id) is None:
            raise BusinessProcessingError(f'Timeline #{timeline_id} is not a timeline of this war room')
    return {
        'mode': mode,
        'date': _validate_date_key(value.get('date'), columns),
        'target': target,
        'war_room_timeline_id': timeline_id,
    }


def war_room_tracker_timelines_check_columns(sync, columns):
    """Refuse columns that drop (or retype) the column dating the events."""
    if sync is None or sync.get('date') == WAR_ROOM_TRACKER_TIMELINE_CHANGED_AT:
        return
    try:
        _validate_date_key(sync.get('date'), columns)
    except BusinessProcessingError:
        raise BusinessProcessingError('This column dates the timeline events: change the timeline date of the '
                                      'tracker first')


# ------------------------------------------------------------ Item links --

def war_room_tracker_timelines_item_links(case_links=(), visibility_case_ids=None, managed_asset_id=None):
    """What a source item brings to the timelines: `case_links`, the
    `(case_id, asset_ids, ioc_ids)` of the cases it belongs to, and who in
    the room may read a war-room event about it."""
    return {
        'case_links': [(case_id, tuple(asset_ids), tuple(ioc_ids)) for case_id, asset_ids, ioc_ids in case_links],
        'visibility_case_ids': sorted(set(visibility_case_ids)) if visibility_case_ids is not None else None,
        'visibility_managed_asset_id': managed_asset_id,
    }


def war_room_tracker_timelines_event_visible(event, readable_case_ids, managed_asset_visible):
    """Whether a room member may read the war-room event: always unless a
    tracker gated it, then when the member reads one of its cases, or sees
    its registry asset (`managed_asset_visible(id)`)."""
    if event.visibility_managed_asset_id is not None:
        return bool(managed_asset_visible(event.visibility_managed_asset_id))
    if event.visibility_case_ids is not None:
        return bool(set(event.visibility_case_ids) & set(readable_case_ids))
    return True


def war_room_tracker_timelines_gated_case_ids(events):
    """Every case id gating one of the events (to check access in one go)."""
    return sorted({case_id for event in events for case_id in (event.visibility_case_ids or ())})


# ----------------------------------------------------------------- Text --

def war_room_tracker_timelines_event_date(sync, columns, values, now):
    """The naive UTC date of an event: the value of the date column when
    set and valid, the change time otherwise."""
    key = sync.get('date')
    if key == WAR_ROOM_TRACKER_TIMELINE_CHANGED_AT:
        return now
    column = next((column for column in columns or () if column.get('key') == key), None)
    raw = (values or {}).get(key)
    if column is None or not isinstance(raw, str):
        return now
    try:
        if column['type'] == 'date':
            day = datetime.date.fromisoformat(raw)
            return datetime.datetime(day.year, day.month, day.day)
        text = raw.strip()
        if text.endswith(('Z', 'z')):
            text = f'{text[:-1]}+00:00'
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return now
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _format_value(column, value, names):
    if value is None or value == '' or value == []:
        return None
    column_type = column.get('type') if column else None
    if column_type == 'checkbox':
        return 'yes' if value else 'no'
    if column_type == 'user' and _is_int(value):
        return names.get(value) or f'user #{value}'
    if isinstance(value, list):
        return ', '.join(str(entry) for entry in value)
    return str(value)


def _step_label(details):
    return details.get('step_name') or f'step #{details.get("step_id")}'


def _title(tracker_name, label, summary):
    title = f'{tracker_name} · {label}: {summary}'
    if len(title) > _TITLE_MAX:
        title = f'{title[:_TITLE_MAX - 1]}…'
    return title


def _quote(text):
    return '\n'.join(f'> {line}' for line in str(text).splitlines() or [''])


def war_room_tracker_timelines_changes_text(tracker_name, label, events, columns, names, author=None):
    """`(title, content)` of a `changes` event. `author` names who made
    the changes (the event itself is created by whoever writes it)."""
    by_key = {column['key']: column for column in columns or ()}
    phrases = []
    lines = []
    for action, details in events:
        if action == 'created':
            phrases.append('added to the tracker')
            lines.append('- Added to the tracker')
        elif action == 'step_added':
            phrases.append(f'{_step_label(details)} set')
            lines.append(f'- Step **{_step_label(details)}** set')
        elif action == 'step_removed':
            phrases.append(f'{_step_label(details)} cleared')
            lines.append(f'- Step **{_step_label(details)}** cleared')
        elif action == 'note_changed':
            if details.get('new'):
                phrases.append('note updated')
                lines.append(f'- Note updated:\n\n{_quote(details["new"])}\n')
            else:
                phrases.append('note cleared')
                lines.append('- Note cleared')
        elif action == 'label_changed':
            phrases.append(f'renamed from "{details.get("old") or ""}"')
            lines.append(f'- Renamed from "{details.get("old") or ""}" to "{details.get("new") or ""}"')
        elif action == 'value_changed':
            column = by_key.get(details.get('column_key'))
            column_label = details.get('column_label') or (column or {}).get('label') or details.get('column_key')
            old = _format_value(column, details.get('old'), names)
            new = _format_value(column, details.get('new'), names)
            phrases.append(f'{column_label}: {new}' if new is not None else f'{column_label} cleared')
            lines.append(f'- **{column_label}**: {old or _EMPTY} → {new or _EMPTY}')
    if author:
        lines.append(f'\n_Changed by {author}_')
    return _title(tracker_name, label, '; '.join(phrases)), '\n'.join(lines)


def war_room_tracker_timelines_state_text(tracker_name, label, step_names, columns, values, note, names,
                                         updated_by=None):
    """`(title, content)` of a `state` event. `step_names` are the names
    of the steps of the row, in the order of the war room; `updated_by`
    names who last changed it."""
    steps = ', '.join(step_names)
    lines = [f'**Steps:** {steps or "none"}']
    for column in columns or ():
        formatted = _format_value(column, (values or {}).get(column['key']), names)
        if formatted is not None:
            lines.append(f'**{column["label"]}:** {formatted}')
    if updated_by:
        lines.append(f'**Last changed by:** {updated_by}')
    if note:
        lines.append(f'\n**Note:**\n\n{note}')
    return _title(tracker_name, label, steps or 'no step'), '  \n'.join(lines)


# --------------------------------------------------------------- Writer --

class WarRoomTrackerTimelineWriter:
    """Writes the timeline events of the rows of one tracker during one
    request. Disabled (every call a no-op) when the tracker has no
    `timeline_sync`."""

    def __init__(self, war_room_id, tracker, writable_case_ids, user_id, now, step_names):
        self._war_room_id = war_room_id
        self._tracker = tracker
        self._sync = tracker.timeline_sync
        self._writable = set(writable_case_ids or ())
        self._user_id = user_id
        self._now = now
        self._step_names = step_names
        self._names = {}
        self._case_timelines = {}
        self._created_case_timelines = set()
        self._touched_case_ids = set()
        self._room_timeline = None
        self._links = {}
        self._events = 0
        self._skipped_case_ids = set()
        self._restricted_rows = 0
        self._war_room_rows = 0

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
        found = war_room_tracker_timelines_db_state_links(row_ids)
        for row_id in row_ids:
            self._links[row_id] = found.get(row_id, [])

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
        """None when off, else `{events, skipped_case_ids, restricted_rows,
        war_room_rows}`: the events written, the cases skipped for lack of
        full access, the rows skipped because their item is not visible
        and, targeting the cases, the rows without a case written to the
        war-room timeline instead."""
        if not self.enabled:
            return None
        return {'events': self._events, 'skipped_case_ids': sorted(self._skipped_case_ids),
                'restricted_rows': self._restricted_rows, 'war_room_rows': self._war_room_rows}

    def finish(self):
        """Log the case activity, once the events are committed."""
        for case_id in sorted(self._created_case_timelines):
            track_activity(f'created timeline "{WAR_ROOM_TRACKER_CASE_TIMELINE_NAME}"', caseid=case_id)
        for case_id in sorted(self._touched_case_ids):
            track_activity(f'tracker "{self._tracker.name}" updated the timeline '
                           f'"{WAR_ROOM_TRACKER_CASE_TIMELINE_NAME}"', caseid=case_id)

    # -- internals

    def _user_names(self, values, events, author_id=None):
        """The names of the users set in the user columns, before and after,
        and of the author."""
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
            self._restricted_rows += 1
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
            author_id = getattr(row, 'updated_by_id', None) or getattr(row, 'created_by_id', None)
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
        links = (item or {}).get('timeline') or war_room_tracker_timelines_item_links()
        if self._sync['target'] == 'cases' and links['case_links']:
            for case_id, asset_ids, ioc_ids in links['case_links']:
                if case_id not in self._writable:
                    self._skipped_case_ids.add(case_id)
                    continue
                self._case_event(row, case_id, asset_ids, ioc_ids, title, content, event_date)
        else:
            if self._sync['target'] == 'cases':
                self._war_room_rows += 1
            self._room_event(row, links, title, content, event_date)

    def _state_link(self, row, case_id):
        if row.id not in self._links:
            self.prefetch([row.id])
        return next((link for link in self._links[row.id] if link.case_id == case_id), None)

    def _save_state_link(self, row, link, case_id, case_event_id=None, war_room_event_id=None):
        if link is None:
            link = war_room_tracker_timelines_db_add(WarRoomTrackerRowTimelineEvent(
                row_id=row.id, case_id=case_id, case_event_id=case_event_id, war_room_event_id=war_room_event_id,
                updated_at=self._now))
            self._links[row.id].append(link)
            return
        link.case_event_id = case_event_id
        link.war_room_event_id = war_room_event_id
        link.updated_at = self._now

    def _case_timeline(self, case_id):
        timeline = self._case_timelines.get(case_id)
        if timeline is None:
            timeline, created = war_room_tracker_timelines_db_case_timeline(
                case_id, WAR_ROOM_TRACKER_CASE_TIMELINE_NAME,
                lambda: CaseTimeline(case_id=case_id, name=WAR_ROOM_TRACKER_CASE_TIMELINE_NAME,
                                     description=_CASE_TIMELINE_DESCRIPTION, color=_TIMELINE_COLOR,
                                     is_default=False, created_by_id=self._user_id))
            if created:
                self._created_case_timelines.add(case_id)
            self._case_timelines[case_id] = timeline
        return timeline

    def _touch_case(self, case_id):
        if case_id not in self._touched_case_ids:
            self._touched_case_ids.add(case_id)
            update_timeline_state(case_id, self._user_id)

    def _case_event(self, row, case_id, asset_ids, ioc_ids, title, content, event_date):
        link = self._state_link(row, case_id) if self.mode == 'state' else None
        event = None
        if link is not None and link.case_event_id is not None:
            event = war_room_tracker_timelines_db_case_event(case_id, link.case_event_id)
        if event is not None:
            event.event_title = title
            event.event_content = content
            event.event_date = event_date
            event.event_date_wtz = event_date
            event.event_tz = '+00:00'
            add_obj_history_entry(event, 'updated')
        else:
            timeline = self._case_timeline(case_id)
            event = CasesEvent(
                case_id=case_id,
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
            war_room_tracker_timelines_db_add(event)
            war_room_tracker_timelines_db_link_case_event(event.event_id, case_id, timeline.timeline_id, asset_ids,
                                                          ioc_ids)
        if self.mode == 'state':
            self._save_state_link(row, link, case_id, case_event_id=event.event_id)
        self._touch_case(case_id)
        self._events += 1

    def _war_room_timeline(self):
        if self._room_timeline is None:
            timeline_id = self._sync.get('war_room_timeline_id')
            if timeline_id is not None:
                self._room_timeline = war_room_tracker_timelines_db_war_room_timeline(self._war_room_id, timeline_id)
            if self._room_timeline is None:
                self._room_timeline, _ = war_room_tracker_timelines_db_war_room_timeline_by_name(
                    self._war_room_id, WAR_ROOM_TRACKER_ROOM_TIMELINE_NAME,
                    lambda: WarRoomTimeline(war_room_id=self._war_room_id, name=WAR_ROOM_TRACKER_ROOM_TIMELINE_NAME,
                                            description=_ROOM_TIMELINE_DESCRIPTION, color=_TIMELINE_COLOR,
                                            is_default=False, created_by_id=self._user_id))
        return self._room_timeline

    def _room_event(self, row, links, title, content, event_date):
        link = self._state_link(row, None) if self.mode == 'state' else None
        event = None
        if link is not None and link.war_room_event_id is not None:
            event = war_room_tracker_timelines_db_war_room_event(self._war_room_id, link.war_room_event_id)
        if event is None:
            event = WarRoomTimelineEvent(timeline_id=self._war_room_timeline().timeline_id, source=_EVENT_SOURCE,
                                         tags=_EVENT_TAGS, is_flagged=False, tracker_id=self._tracker.id,
                                         created_by_id=self._user_id)
        event.title = title
        event.content = content
        event.event_date = event_date
        event.event_tz = '+00:00'
        event.visibility_case_ids = links['visibility_case_ids']
        event.visibility_managed_asset_id = links['visibility_managed_asset_id']
        if event.id is None:
            war_room_tracker_timelines_db_add(event)
        if self.mode == 'state':
            self._save_state_link(row, link, None, war_room_event_id=event.id)
        self._events += 1
