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

"""Case trackers: user-defined tabs of a case whose rows carry case
steps, a note and extra column values.

The case counterpart of the war-room trackers (`war_room_trackers`),
whose validation and state helpers are shared. A tracker is either
`manual` (rows added by hand, free text or case items) or mirrors the
case (`assets`, `iocs`, `tasks`, `evidences`). A mirror's rows are the
current items of the case merged with the stored state of each item
(`CaseTrackerRow`, created lazily on the first change). The state of an
item deleted from the case is kept, hidden.

Authorization is NOT done here: the blueprint checks the case access.
Every member of the case sees every row; a manual row whose item was
deleted is returned as `restricted`, without the item.

A tracker can write its rows to a timeline of the case, see
`case_tracker_timelines`.
"""

import datetime

from app.business.case_tracker_timelines import CaseTrackerTimelineWriter
from app.business.case_tracker_timelines import case_tracker_timelines_item_links
from app.business.case_tracker_timelines import case_tracker_timelines_validate
from app.business.war_room_scope import war_room_scope_list_assets
from app.business.war_room_scope import war_room_scope_list_iocs
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_check_columns
from app.business.war_room_trackers import war_room_trackers_apply_state
from app.business.war_room_trackers import war_room_trackers_describe_changes
from app.business.war_room_trackers import war_room_trackers_describe_rows
from app.business.war_room_trackers import war_room_trackers_parse_label
from app.business.war_room_trackers import war_room_trackers_parse_state_changes
from app.business.war_room_trackers import war_room_trackers_reorder
from app.business.war_room_trackers import war_room_trackers_serialize_step
from app.business.war_room_trackers import war_room_trackers_validate_color
from app.business.war_room_trackers import war_room_trackers_validate_columns
from app.business.war_room_trackers import war_room_trackers_validate_icon
from app.business.war_room_trackers import war_room_trackers_values_before
from app.datamgmt.case.case_trackers_db import case_trackers_db_add
from app.datamgmt.case.case_trackers_db import case_trackers_db_commit
from app.datamgmt.case.case_trackers_db import case_trackers_db_delete
from app.datamgmt.case.case_trackers_db import case_trackers_db_events
from app.datamgmt.case.case_trackers_db import case_trackers_db_events_not_in_timeline
from app.datamgmt.case.case_trackers_db import case_trackers_db_events_not_in_timeline_count
from app.datamgmt.case.case_trackers_db import case_trackers_db_evidences
from app.datamgmt.case.case_trackers_db import case_trackers_db_row_counts
from app.datamgmt.case.case_trackers_db import case_trackers_db_row_step_remove
from app.datamgmt.case.case_trackers_db import case_trackers_db_row_steps
from app.datamgmt.case.case_trackers_db import case_trackers_db_rows
from app.datamgmt.case.case_trackers_db import case_trackers_db_rows_by_ids
from app.datamgmt.case.case_trackers_db import case_trackers_db_rows_keys
from app.datamgmt.case.case_trackers_db import case_trackers_db_rows_max_sort
from app.datamgmt.case.case_trackers_db import case_trackers_db_step_find_by_name
from app.datamgmt.case.case_trackers_db import case_trackers_db_step_get
from app.datamgmt.case.case_trackers_db import case_trackers_db_steps
from app.datamgmt.case.case_trackers_db import case_trackers_db_task_assignees
from app.datamgmt.case.case_trackers_db import case_trackers_db_tasks
from app.datamgmt.case.case_trackers_db import case_trackers_db_tracker_get
from app.datamgmt.case.case_trackers_db import case_trackers_db_tracker_lock
from app.datamgmt.case.case_trackers_db import case_trackers_db_trackers
from app.datamgmt.case.case_trackers_db import case_trackers_db_value_events_after
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_asset_flags
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_user_names
from app.iris_engine.utils.tracker import track_activity
from app.models.case_trackers import CASE_TRACKER_ITEM_TYPES
from app.models.case_trackers import CASE_TRACKER_SOURCES
from app.models.case_trackers import CaseTracker
from app.models.case_trackers import CaseTrackerRow
from app.models.case_trackers import CaseTrackerRowEvent
from app.models.case_trackers import CaseTrackerRowStep
from app.models.case_trackers import CaseTrackerStep
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError


CASE_TRACKERS_ROWS_LIMIT = 5000
CASE_TRACKERS_MAX_STEPS = 50
CASE_TRACKERS_MAX_TRACKERS = 50
CASE_TRACKERS_MAX_BATCH = 500
CASE_TRACKERS_HISTORY_LIMIT = 200
# Past changes written to the timeline per backfill call.
CASE_TRACKERS_TIMELINE_BACKFILL_LIMIT = 2000

_STEP_NAME_MAX = 64
_TRACKER_NAME_MAX = 128
_DESCRIPTION_MAX = 4000
_LABEL_MAX = 512
_NOTE_MAX = 20000

_SOURCE_ITEM_TYPES = {
    'assets': 'asset',
    'iocs': 'ioc',
    'tasks': 'task',
    'evidences': 'evidence',
}
_ITEM_TYPE_SOURCES = {item_type: source for source, item_type in _SOURCE_ITEM_TYPES.items()}

_ITEM_NOT_IN_CASE = 'is not in this case'


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _isoformat(value):
    return value.isoformat() if value is not None else None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _user_ref(user_id, names):
    if user_id is None:
        return None
    return {'id': user_id, 'name': names.get(user_id)}


# ----------------------------------------------------------- Validation --

def _validate_name(value, field, max_len):
    if not isinstance(value, str) or not value.strip():
        raise BusinessProcessingError(f'{field} is required')
    value = value.strip()
    if len(value) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return value


def _validate_description(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('description must be a string')
    value = value.strip()
    if len(value) > _DESCRIPTION_MAX:
        raise BusinessProcessingError(f'description must be at most {_DESCRIPTION_MAX} characters')
    return value or None


def _validate_bool(value, field):
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def case_trackers_validate_source(value):
    if not isinstance(value, str) or value not in CASE_TRACKER_SOURCES:
        raise BusinessProcessingError(f'source must be one of: {", ".join(CASE_TRACKER_SOURCES)}')
    return value


def _validate_id_list(value, field, max_len=CASE_TRACKERS_MAX_BATCH):
    """De-duplicated list of positive integers, order kept."""
    if not isinstance(value, list):
        raise BusinessProcessingError(f'{field} must be a list of integers')
    out = []
    for item in value:
        if not _is_int(item) or item <= 0:
            raise BusinessProcessingError(f'{field} must be a list of integers')
        if item not in out:
            out.append(item)
    if len(out) > max_len:
        raise BusinessProcessingError(f'{field} accepts at most {max_len} entries')
    return out


def _validate_note(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError('note must be a string')
    if len(value) > _NOTE_MAX:
        raise BusinessProcessingError(f'note must be at most {_NOTE_MAX} characters')
    return value if value.strip() else None


def _validate_label(value):
    return _validate_name(value, 'label', _LABEL_MAX)


# ---------------------------------------------------------------- Steps --

def case_trackers_serialize_step(step):
    return war_room_trackers_serialize_step(step)


def case_trackers_steps_list(case_id):
    return case_trackers_db_steps(case_id)


def _step_get(case_id, step_id):
    step = case_trackers_db_step_get(case_id, step_id)
    if step is None:
        raise ObjectNotFoundError()
    return step


def _validate_step_name(case_id, value, exclude_id=None):
    name = _validate_name(value, 'Step name', _STEP_NAME_MAX)
    if case_trackers_db_step_find_by_name(case_id, name, exclude_id=exclude_id) is not None:
        raise BusinessProcessingError(f'A step named "{name}" already exists')
    return name


def _next_sort(objects):
    return max((obj.sort_order for obj in objects), default=-1) + 1


def case_trackers_step_create(case_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    steps = case_trackers_db_steps(case_id)
    if len(steps) >= CASE_TRACKERS_MAX_STEPS:
        raise BusinessProcessingError(f'A case has at most {CASE_TRACKERS_MAX_STEPS} steps')
    color = data.get('color')
    step = case_trackers_db_add(CaseTrackerStep(
        case_id=case_id,
        name=_validate_step_name(case_id, data.get('name')),
        description=_validate_description(data.get('description')),
        color=war_room_trackers_validate_color(color) if color is not None else 'slate',
        icon=war_room_trackers_validate_icon(data.get('icon')),
        sort_order=_next_sort(steps),
    ))
    if not case_trackers_db_commit():
        raise BusinessProcessingError('A step with this name was created meanwhile')
    track_activity(f'created tracker step "{step.name}"', caseid=case_id)
    return step


def case_trackers_step_update(case_id, step_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    step = _step_get(case_id, step_id)
    if 'name' in data:
        step.name = _validate_step_name(case_id, data['name'], exclude_id=step.id)
    if 'description' in data:
        step.description = _validate_description(data['description'])
    if 'color' in data:
        step.color = war_room_trackers_validate_color(data['color'])
    if 'icon' in data:
        step.icon = war_room_trackers_validate_icon(data['icon'])
    if not case_trackers_db_commit():
        raise BusinessProcessingError('A step with this name was created meanwhile')
    return step


def case_trackers_step_delete(case_id, step_id):
    """Delete a step: the rows lose it (FK cascade) and it is pruned from
    the steps offered by the trackers."""
    step = _step_get(case_id, step_id)
    for tracker in case_trackers_db_trackers(case_id):
        if tracker.step_ids and step.id in tracker.step_ids:
            tracker.step_ids = [sid for sid in tracker.step_ids if sid != step.id]
    name = step.name
    case_trackers_db_delete(step)
    case_trackers_db_commit()
    track_activity(f'deleted tracker step "{name}"', caseid=case_id)


def case_trackers_steps_reorder(case_id, ids):
    ordered = war_room_trackers_reorder(case_trackers_db_steps(case_id), ids)
    case_trackers_db_commit()
    return ordered


# -------------------------------------------------------------- Sources --

def _asset_flag_entries(flags, flag_meta):
    out = []
    for flag in flags or ():
        name, color, icon = flag_meta.get(flag['flag_id'], (None, None, None))
        out.append({'flag_id': flag['flag_id'], 'name': name, 'color': color, 'icon': icon,
                    'reason': flag.get('reason'), 'set_at': flag.get('set_at')})
    return out


def _item(item_type, item_id, label, summary, timeline=None):
    """A source item. `timeline` is what it brings to the timeline events
    (see `case_tracker_timelines_item_links`); never sent to the client."""
    return {'item_type': item_type, 'item_id': item_id, 'label': label, 'item': summary,
            'timeline': timeline or case_tracker_timelines_item_links()}


def _source_assets(case_id):
    result = war_room_scope_list_assets([case_id], include_vulnerabilities=False)
    flag_meta = war_room_trackers_db_asset_flags()
    items = []
    for asset in result['data']:
        items.append(_item('asset', asset['asset_id'], asset['asset_name'], {
            'asset_id': asset['asset_id'],
            'asset_name': asset['asset_name'],
            'asset_type': asset.get('asset_type_name'),
            'asset_ip': asset.get('asset_ip'),
            'asset_domain': asset.get('asset_domain'),
            'compromise_status_id': asset.get('asset_compromise_status_id'),
            'analysis_status_id': asset.get('analysis_status_id'),
            'analysis_status_name': asset.get('analysis_status_name'),
            'flags': _asset_flag_entries(asset.get('flags'), flag_meta),
        }, timeline=case_tracker_timelines_item_links(asset_ids=(asset['asset_id'],))))
    return items, bool(result.get('truncated'))


def _source_iocs(case_id):
    result = war_room_scope_list_iocs([case_id])
    items = [_item('ioc', ioc['ioc_id'], ioc['ioc_value'], {
        'ioc_id': ioc['ioc_id'],
        'ioc_value': ioc['ioc_value'],
        'ioc_type': ioc.get('ioc_type_name'),
        'tlp': ioc.get('tlp_name'),
    }, timeline=case_tracker_timelines_item_links(ioc_ids=(ioc['ioc_id'],))) for ioc in result['data']]
    return items, bool(result.get('truncated'))


def _source_tasks(case_id):
    rows = case_trackers_db_tasks(case_id, CASE_TRACKERS_ROWS_LIMIT)
    truncated = len(rows) > CASE_TRACKERS_ROWS_LIMIT
    rows = rows[:CASE_TRACKERS_ROWS_LIMIT]
    assignees = case_trackers_db_task_assignees([row.id for row in rows])
    items = [_item('task', row.id, row.task_title or f'Task #{row.id}', {
        'task_id': row.id,
        'task_title': row.task_title,
        'status_id': row.task_status_id,
        'status_name': row.status_name,
        'status_color': row.status_bscolor,
        'assignees': [{'id': user_id, 'name': name} for user_id, name in assignees.get(row.id, ())],
        'open_date': _isoformat(row.task_open_date),
        'close_date': _isoformat(row.task_close_date),
    }) for row in rows]
    return items, truncated


def _source_evidences(case_id):
    rows = case_trackers_db_evidences(case_id, CASE_TRACKERS_ROWS_LIMIT)
    truncated = len(rows) > CASE_TRACKERS_ROWS_LIMIT
    items = [_item('evidence', row.id, row.filename or f'Evidence #{row.id}', {
        'evidence_id': row.id,
        'filename': row.filename,
        'type': row.type_name,
        'file_size': row.file_size,
        'file_hash': row.file_hash,
        'date_added': _isoformat(row.date_added),
        'acquisition_date': _isoformat(row.acquisition_date),
    }) for row in rows[:CASE_TRACKERS_ROWS_LIMIT]]
    return items, truncated


_SOURCE_LISTERS = {
    'assets': _source_assets,
    'iocs': _source_iocs,
    'tasks': _source_tasks,
    'evidences': _source_evidences,
}


class _SourceCache:
    """The source items, listed at most once per request."""

    def __init__(self, case_id):
        self._case_id = case_id
        self._items = {}

    def items(self, source):
        if source not in self._items:
            self._items[source] = _SOURCE_LISTERS[source](self._case_id)
        return self._items[source]

    def index(self, item_type):
        """`{item id: item}` of the items of `item_type`."""
        items, _ = self.items(_ITEM_TYPE_SOURCES[item_type])
        return {item['item_id']: item for item in items}


# ------------------------------------------------------------- Trackers --

def _validate_step_ids(value, case_step_ids):
    if value is None:
        return None
    step_ids = _validate_id_list(value, 'step_ids', max_len=CASE_TRACKERS_MAX_STEPS)
    unknown = [step_id for step_id in step_ids if step_id not in case_step_ids]
    if unknown:
        raise BusinessProcessingError(f'Unknown step #{unknown[0]}')
    return step_ids


def case_trackers_build_tracker(case_id, data, case_step_ids, sort_order, user_id):
    """An unsaved tracker from a create payload (validated)."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    show_asset_flags = data.get('show_asset_flags')
    columns = war_room_trackers_validate_columns(data.get('columns'))
    now = _utcnow()
    return CaseTracker(
        case_id=case_id,
        name=_validate_name(data.get('name'), 'Tracker name', _TRACKER_NAME_MAX),
        source=case_trackers_validate_source(data.get('source')),
        icon=war_room_trackers_validate_icon(data.get('icon')),
        description=_validate_description(data.get('description')),
        step_ids=_validate_step_ids(data.get('step_ids'), case_step_ids),
        columns=columns,
        show_asset_flags=_validate_bool(show_asset_flags, 'show_asset_flags') if show_asset_flags is not None
        else False,
        timeline_sync=case_tracker_timelines_validate(data.get('timeline_sync'), columns, case_id),
        sort_order=sort_order,
        created_by_id=user_id,
        created_at=now,
        updated_at=now,
    )


def case_trackers_serialize(tracker, row_count, names, timeline_backfill=0):
    """`timeline_backfill`: with a `changes` timeline sync, the past
    changes of the rows not in the timeline yet (made before the sync was
    on)."""
    return {
        'id': tracker.id,
        'case_id': tracker.case_id,
        'name': tracker.name,
        'icon': tracker.icon,
        'description': tracker.description,
        'source': tracker.source,
        'step_ids': tracker.step_ids,
        'columns': tracker.columns or [],
        'show_asset_flags': bool(tracker.show_asset_flags),
        'timeline_sync': tracker.timeline_sync,
        'timeline_backfill': timeline_backfill,
        'sort_order': tracker.sort_order,
        'row_count': row_count,
        'created_by': _user_ref(tracker.created_by_id, names),
        'created_at': _isoformat(tracker.created_at),
        'updated_at': _isoformat(tracker.updated_at),
    }


def _writes_changes(tracker):
    return (tracker.timeline_sync or {}).get('mode') == 'changes'


def case_trackers_serialize_many(case_id, trackers):
    """Trackers with their number of rows and, for a `changes` timeline
    sync, the past changes not in the timeline yet."""
    cache = _SourceCache(case_id)
    stored = case_trackers_db_row_counts([t.id for t in trackers if t.source == 'manual'])
    names = war_room_trackers_db_user_names([t.created_by_id for t in trackers])
    out = []
    for tracker in trackers:
        if tracker.source == 'manual':
            count = stored.get(tracker.id, 0)
        else:
            count = len(cache.items(tracker.source)[0])
        backfill = 0
        if _writes_changes(tracker):
            backfill = case_trackers_db_events_not_in_timeline_count(
                tracker.id, [row.id for row in case_trackers_db_rows(tracker.id)])
        out.append(case_trackers_serialize(tracker, min(count, CASE_TRACKERS_ROWS_LIMIT), names, backfill))
    return out


def case_trackers_list(case_id):
    return case_trackers_db_trackers(case_id)


def case_trackers_get(case_id, tracker_id):
    tracker = case_trackers_db_tracker_get(case_id, tracker_id)
    if tracker is None:
        raise ObjectNotFoundError()
    return tracker


def case_trackers_create(case_id, data, user_id):
    trackers = case_trackers_db_trackers(case_id)
    if len(trackers) >= CASE_TRACKERS_MAX_TRACKERS:
        raise BusinessProcessingError(f'A case has at most {CASE_TRACKERS_MAX_TRACKERS} trackers')
    case_step_ids = {step.id for step in case_trackers_db_steps(case_id)}
    tracker = case_trackers_build_tracker(case_id, data, case_step_ids, _next_sort(trackers), user_id)
    case_trackers_db_add(tracker)
    case_trackers_db_commit()
    track_activity(f'created tracker "{tracker.name}"', caseid=case_id)
    return tracker


def _merge_column_keys(current, columns):
    """Columns sent back without their key but with the label of an
    existing column keep that column's key (its values stay attached)."""
    by_label = {column['label'].lower(): column['key'] for column in current or ()}
    taken = {column['key'] for column in columns if isinstance(column, dict) and column.get('key')}
    out = []
    for column in columns:
        if isinstance(column, dict) and not column.get('key') and isinstance(column.get('label'), str):
            key = by_label.get(column['label'].strip().lower())
            if key is not None and key not in taken:
                column = {**column, 'key': key}
                taken.add(key)
        out.append(column)
    return out


def case_trackers_update(case_id, tracker_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    tracker = case_trackers_get(case_id, tracker_id)
    if 'source' in data and data['source'] != tracker.source:
        raise BusinessProcessingError('The source of a tracker cannot be changed')
    if 'name' in data:
        tracker.name = _validate_name(data['name'], 'Tracker name', _TRACKER_NAME_MAX)
    if 'icon' in data:
        tracker.icon = war_room_trackers_validate_icon(data['icon'])
    if 'description' in data:
        tracker.description = _validate_description(data['description'])
    if 'step_ids' in data:
        case_step_ids = {step.id for step in case_trackers_db_steps(case_id)}
        tracker.step_ids = _validate_step_ids(data['step_ids'], case_step_ids)
    if 'columns' in data:
        columns = data['columns']
        if isinstance(columns, list):
            columns = _merge_column_keys(tracker.columns, columns)
        tracker.columns = war_room_trackers_validate_columns(columns)
    if 'show_asset_flags' in data:
        tracker.show_asset_flags = _validate_bool(data['show_asset_flags'], 'show_asset_flags')
    if 'timeline_sync' in data:
        tracker.timeline_sync = case_tracker_timelines_validate(data['timeline_sync'], tracker.columns, case_id)
    elif 'columns' in data:
        war_room_tracker_timelines_check_columns(tracker.timeline_sync, tracker.columns)
    tracker.updated_at = _utcnow()
    case_trackers_db_commit()
    return tracker


def case_trackers_delete(case_id, tracker_id):
    tracker = case_trackers_get(case_id, tracker_id)
    name = tracker.name
    case_trackers_db_delete(tracker)
    case_trackers_db_commit()
    track_activity(f'deleted tracker "{name}"', caseid=case_id)


def case_trackers_trackers_reorder(case_id, ids):
    ordered = war_room_trackers_reorder(case_trackers_db_trackers(case_id), ids)
    case_trackers_db_commit()
    return ordered


# ----------------------------------------------------------------- Rows --

def _row_key(item_type, item_id, row_id, manual):
    """`row:<id>` for the rows of a manual tracker (an item can have
    several), `<item_type>:<item_id>` for the items of a mirror (the same
    before and after their row is stored)."""
    if item_type is not None and not manual:
        return f'{item_type}:{item_id}'
    return f'row:{row_id}'


def _row_payload(stored, item, steps, names, manual):
    """A row: `stored` is the stored state (None for a mirrored item not
    touched yet), `item` the source item (None for free text, or for an
    item deleted from the case). `label` is the item's own label for an
    item row, `custom_label` the one given to the row."""
    if stored is None:
        return {
            'row_id': None, 'key': _row_key(item['item_type'], item['item_id'], None, manual),
            'item_type': item['item_type'], 'item_id': item['item_id'], 'label': item['label'],
            'custom_label': None, 'item': item['item'], 'restricted': False, 'steps': [], 'note': None,
            'values': {}, 'updated_at': None, 'updated_by': None,
        }
    if stored.item_type is None:
        label = stored.label
    else:
        label = item['label'] if item is not None else None
    return {
        'row_id': stored.id,
        'key': _row_key(stored.item_type, stored.item_id, stored.id, manual),
        'item_type': stored.item_type,
        'item_id': stored.item_id,
        'label': label,
        'custom_label': stored.label if stored.item_type is not None else None,
        'item': item['item'] if item is not None else None,
        'restricted': stored.item_type is not None and item is None,
        'steps': [{'step_id': step.step_id, 'set_by': _user_ref(step.set_by_id, names),
                   'set_at': _isoformat(step.set_at)} for step in steps.get(stored.id, ())],
        'note': stored.note,
        'values': dict(stored.values or {}),
        'updated_at': _isoformat(stored.updated_at),
        'updated_by': _user_ref(stored.updated_by_id, names),
    }


def _rows_payload(pairs, manual):
    stored_ids = [stored.id for stored, _ in pairs if stored is not None]
    steps = {}
    user_ids = {stored.updated_by_id for stored, _ in pairs if stored is not None}
    for step in case_trackers_db_row_steps(stored_ids):
        steps.setdefault(step.row_id, []).append(step)
        user_ids.add(step.set_by_id)
    names = war_room_trackers_db_user_names(user_ids)
    return [_row_payload(stored, item, steps, names, manual) for stored, item in pairs]


def _stored_by_item(tracker):
    return {(row.item_type, row.item_id): row for row in case_trackers_db_rows(tracker.id)
            if row.item_type is not None}


def _mirror_pairs(tracker, cache):
    items, truncated = cache.items(tracker.source)
    stored = _stored_by_item(tracker)
    return [(stored.get((item['item_type'], item['item_id'])), item) for item in items], truncated


def _manual_pairs(stored_rows, cache):
    types = {row.item_type for row in stored_rows if row.item_type is not None}
    indexes = {item_type: cache.index(item_type) for item_type in types}
    return [(row, indexes[row.item_type].get(row.item_id) if row.item_type is not None else None)
            for row in stored_rows]


def _tracker_pairs(tracker, cache):
    """`(pairs, truncated)`: the `(stored, item)` rows of a tracker, at
    most `CASE_TRACKERS_ROWS_LIMIT`."""
    limit = CASE_TRACKERS_ROWS_LIMIT
    if tracker.source == 'manual':
        stored = case_trackers_db_rows(tracker.id, limit=limit)
        return _manual_pairs(stored[:limit], cache), len(stored) > limit
    pairs, truncated = _mirror_pairs(tracker, cache)
    return pairs[:limit], truncated or len(pairs) > limit


def case_trackers_rows_list(case_id, tracker):
    """`{data, truncated, limit}`: a mirror lists every current item of
    the case merged with its stored state, a manual tracker its stored
    rows."""
    pairs, truncated = _tracker_pairs(tracker, _SourceCache(case_id))
    return {'data': _rows_payload(pairs, tracker.source == 'manual'), 'truncated': truncated,
            'limit': CASE_TRACKERS_ROWS_LIMIT}


def _offered_step_ids(tracker, case_steps):
    case_step_ids = [step.id for step in case_steps]
    if tracker.step_ids is None:
        return set(case_step_ids)
    return set(tracker.step_ids) & set(case_step_ids)


def _parse_row_refs(value):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError('rows must be a non-empty list')
    if len(value) > CASE_TRACKERS_MAX_BATCH:
        raise BusinessProcessingError(f'At most {CASE_TRACKERS_MAX_BATCH} rows at once')
    refs = []
    for ref in value:
        if not isinstance(ref, dict):
            raise BusinessProcessingError('Each row must be an object')
        if ref.get('row_id') is not None:
            if not _is_int(ref['row_id']) or ref['row_id'] <= 0:
                raise BusinessProcessingError('row_id must be an integer')
            refs.append(('row', ref['row_id']))
            continue
        item_type = ref.get('item_type')
        item_id = ref.get('item_id')
        if item_type not in CASE_TRACKER_ITEM_TYPES or not _is_int(item_id) or item_id <= 0:
            raise BusinessProcessingError('Each row needs a row_id, or an item_type and an item_id')
        refs.append((item_type, item_id))
    return refs


def _resolve_mirror_targets(tracker, refs, cache):
    """`[(stored or None, item)]` of the refs; every one must be a current
    item of the case."""
    items, _ = cache.items(tracker.source)
    item_type = _SOURCE_ITEM_TYPES[tracker.source]
    by_id = {item['item_id']: item for item in items}
    stored_rows = [row for row in case_trackers_db_rows(tracker.id) if row.item_type is not None]
    stored_by_item = {(row.item_type, row.item_id): row for row in stored_rows}
    stored_by_id = {row.id: row for row in stored_rows}
    targets = []
    seen = set()
    for kind, ref_id in refs:
        if kind == 'row':
            stored = stored_by_id.get(ref_id)
            if stored is None:
                raise BusinessProcessingError(f'Row #{ref_id} not found in this tracker')
            item = by_id.get(stored.item_id)
        elif kind == item_type:
            item = by_id.get(ref_id)
        else:
            raise BusinessProcessingError(f'This tracker lists {item_type} items only')
        if item is None:
            raise BusinessProcessingError(f'{kind.capitalize()} #{ref_id} {_ITEM_NOT_IN_CASE}')
        key = (item['item_type'], item['item_id'])
        if key in seen:
            continue
        seen.add(key)
        targets.append((stored_by_item.get(key), item))
    return targets


def _resolve_manual_targets(tracker, refs, cache):
    """The stored rows of the refs; an item ref targets every row of the
    item."""
    stored_rows = case_trackers_db_rows(tracker.id)
    by_id = {row.id: [row] for row in stored_rows}
    by_item = {}
    for row in stored_rows:
        if row.item_type is not None:
            by_item.setdefault((row.item_type, row.item_id), []).append(row)
    rows = []
    for kind, ref_id in refs:
        matches = by_id.get(ref_id) if kind == 'row' else by_item.get((kind, ref_id))
        if not matches:
            raise BusinessProcessingError(f'Row {kind}:{ref_id} not found in this tracker')
        rows.extend(stored for stored in matches if stored not in rows)
    return _manual_pairs(rows, cache)


def _record(tracker_id, row_id, user_id, now, events, in_timeline):
    for action, details in events:
        case_trackers_db_add(CaseTrackerRowEvent(tracker_id=tracker_id, row_id=row_id, user_id=user_id,
                                                 created_at=now, action=action, details=details,
                                                 in_timeline=in_timeline))


def _with_timeline(result, writer):
    summary = writer.summary()
    if summary is not None:
        result['timeline'] = summary
    return result


def case_trackers_rows_update_state(case_id, tracker, body, user_id):
    """Bulk update of the steps / note / values / label of rows. Mirrored
    items get their stored row on their first actual change. One event
    per actual change. Returns `{data: [Row]}` (the targeted rows), with
    the `timeline` summary when the tracker writes to the timeline."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid request')
    refs = _parse_row_refs(body.get('rows'))
    case_steps = case_trackers_db_steps(case_id)
    changes = war_room_trackers_parse_state_changes(body, tracker, case_steps)
    cache = _SourceCache(case_id)
    if tracker.source == 'manual':
        targets = _resolve_manual_targets(tracker, refs, cache)
    else:
        case_trackers_db_tracker_lock(tracker.id)
        targets = _resolve_mirror_targets(tracker, refs, cache)

    step_names = {step.id: step.name for step in case_steps}
    current_steps = {}
    for step in case_trackers_db_row_steps([stored.id for stored, _ in targets if stored is not None]):
        current_steps.setdefault(step.row_id, set()).add(step.step_id)
    now = _utcnow()
    writer = CaseTrackerTimelineWriter(case_id, tracker, user_id, now, step_names)
    writer.prefetch([stored.id for stored, _ in targets if stored is not None])
    result = []
    applied = []
    for stored, item in targets:
        row = stored
        if row is None:
            row = CaseTrackerRow(tracker_id=tracker.id, item_type=item['item_type'], item_id=item['item_id'],
                                 note=None, values={}, sort_order=0, created_by_id=user_id, created_at=now)
        to_add, to_remove, events = war_room_trackers_apply_state(
            row, current_steps.get(row.id, ()) if stored is not None else (), changes, tracker.columns, step_names)
        if events:
            row.updated_at = now
            row.updated_by_id = user_id
            if stored is None:
                case_trackers_db_add(row)
            for step_id in to_remove:
                case_trackers_db_row_step_remove(row.id, step_id)
            for step_id in to_add:
                case_trackers_db_add(CaseTrackerRowStep(row_id=row.id, step_id=step_id, set_by_id=user_id,
                                                        set_at=now))
            _record(tracker.id, row.id, user_id, now, events, writer.mode == 'changes')
            applied.append((row.item_type, row.label, events))
            step_ids = (set(current_steps.get(row.id, ())) - set(to_remove)) | set(to_add)
            writer.row_changed(row, item, events, step_ids)
        result.append((row if row.id is not None else None, item))
    if not case_trackers_db_commit():
        raise BusinessProcessingError('These rows were changed meanwhile, reload and retry')
    writer.finish()
    description = war_room_trackers_describe_changes(applied)
    if description:
        track_activity(f'tracker "{tracker.name}": {description}', caseid=case_id)
    return _with_timeline({'data': _rows_payload(result, tracker.source == 'manual')}, writer)


def _parse_new_rows(value):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError('rows must be a non-empty list')
    if len(value) > CASE_TRACKERS_MAX_BATCH:
        raise BusinessProcessingError(f'At most {CASE_TRACKERS_MAX_BATCH} rows at once')
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            raise BusinessProcessingError('Each row must be an object')
        if entry.get('item_type') is not None:
            item_type = entry['item_type']
            item_id = entry.get('item_id')
            if item_type not in CASE_TRACKER_ITEM_TYPES:
                raise BusinessProcessingError(f'item_type must be one of: {", ".join(CASE_TRACKER_ITEM_TYPES)}')
            if not _is_int(item_id) or item_id <= 0:
                raise BusinessProcessingError('item_id must be an integer')
            out.append((item_type, item_id, war_room_trackers_parse_label(entry.get('label'))))
        else:
            out.append((None, None, _validate_label(entry.get('label'))))
    return out


def case_trackers_rows_add(case_id, tracker, body, user_id):
    """Add rows to a manual tracker: free text (`label`) or items of the
    case (with an optional custom `label`). Items already in the tracker
    are skipped, unless `allow_duplicates`. Returns `{data: [Row]}` (the
    created rows)."""
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows can only be added to a manual tracker')
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid request')
    entries = _parse_new_rows(body.get('rows'))
    case_steps = case_trackers_db_steps(case_id)
    step_ids = []
    if body.get('step_ids') is not None:
        offered = _offered_step_ids(tracker, case_steps)
        step_ids = _validate_id_list(body['step_ids'], 'step_ids', max_len=CASE_TRACKERS_MAX_STEPS)
        unknown = [step_id for step_id in step_ids if step_id not in offered]
        if unknown:
            raise BusinessProcessingError(f'Step #{unknown[0]} is not offered by this tracker')
    note = _validate_note(body.get('note'))
    allow_duplicates = _validate_bool(body.get('allow_duplicates', False), 'allow_duplicates')

    cache = _SourceCache(case_id)
    indexes = {}
    for item_type, item_id, _ in entries:
        if item_type is None:
            continue
        if item_type not in indexes:
            indexes[item_type] = cache.index(item_type)
        if item_id not in indexes[item_type]:
            raise BusinessProcessingError(f'{item_type.capitalize()} #{item_id} {_ITEM_NOT_IN_CASE}')

    existing = case_trackers_db_rows(tracker.id)
    if len(existing) + len(entries) > CASE_TRACKERS_ROWS_LIMIT:
        raise BusinessProcessingError(f'A tracker holds at most {CASE_TRACKERS_ROWS_LIMIT} rows')
    present = {(row.item_type, row.item_id) for row in existing if row.item_type is not None}
    step_names = {step.id: step.name for step in case_steps}
    sort_order = case_trackers_db_rows_max_sort(tracker.id) + 1
    now = _utcnow()
    writer = CaseTrackerTimelineWriter(case_id, tracker, user_id, now, step_names)
    created = []
    for item_type, item_id, label in entries:
        if item_type is not None and not allow_duplicates:
            if (item_type, item_id) in present:
                continue
            present.add((item_type, item_id))
        row = case_trackers_db_add(CaseTrackerRow(
            tracker_id=tracker.id, item_type=item_type, item_id=item_id, label=label, note=note, values={},
            sort_order=sort_order, created_by_id=user_id, created_at=now, updated_by_id=user_id, updated_at=now,
        ))
        sort_order += 1
        events = [('created', {'label': label} if label is not None else {})]
        for step_id in step_ids:
            case_trackers_db_add(CaseTrackerRowStep(row_id=row.id, step_id=step_id, set_by_id=user_id, set_at=now))
            events.append(('step_added', {'step_id': step_id, 'step_name': step_names.get(step_id)}))
        _record(tracker.id, row.id, user_id, now, events, writer.mode == 'changes')
        item = indexes[item_type].get(item_id) if item_type is not None else None
        writer.row_changed(row, item, events, set(step_ids))
        created.append((row, item))
    if not case_trackers_db_commit():
        raise BusinessProcessingError('Some of these items were added meanwhile, reload and retry')
    writer.finish()
    if created:
        track_activity(f'tracker "{tracker.name}": added '
                       f'{war_room_trackers_describe_rows([(row.item_type, row.label) for row, _ in created])}',
                       caseid=case_id)
    return _with_timeline({'data': _rows_payload(created, True)}, writer)


def _stored_pairs(tracker, cache):
    """`{row_id: (stored, item)}` of every stored row of a tracker (item
    None for free text and for items deleted from the case)."""
    stored_rows = case_trackers_db_rows(tracker.id)
    if tracker.source == 'manual':
        pairs = _manual_pairs(stored_rows, cache)
    else:
        index = cache.index(_SOURCE_ITEM_TYPES[tracker.source])
        pairs = [(row, index.get(row.item_id)) for row in stored_rows]
    return {stored.id: (stored, item) for stored, item in pairs}


def case_trackers_timeline_sync(case_id, tracker, user_id):
    """Catch the timeline up with a tracker whose sync was turned on after
    the fact: `state` mode writes the current state of every stored row,
    `changes` mode the past changes not in the timeline yet
    (`_timeline_backfill`). Returns the timeline summary."""
    if tracker.timeline_sync is None:
        raise BusinessProcessingError('This tracker does not write to the timeline')
    if _writes_changes(tracker):
        return _timeline_backfill(case_id, tracker, user_id)
    pairs = list(_stored_pairs(tracker, _SourceCache(case_id)).values())
    step_names = {step.id: step.name for step in case_trackers_db_steps(case_id)}
    steps = {}
    for step in case_trackers_db_row_steps([stored.id for stored, _ in pairs]):
        steps.setdefault(step.row_id, set()).add(step.step_id)
    writer = CaseTrackerTimelineWriter(case_id, tracker, user_id, _utcnow(), step_names)
    writer.prefetch([stored.id for stored, _ in pairs])
    for stored, item in pairs:
        writer.row_state(stored, item, steps.get(stored.id, set()))
    if not case_trackers_db_commit():
        raise BusinessProcessingError('The timeline was changed meanwhile, retry')
    writer.finish()
    return writer.summary()


def _history_groups(events):
    """Past changes grouped as they were made: one group per row and per
    update, oldest first. A batch cut by the limit leaves its last group
    for the next call (unless it is the only one)."""
    groups = {}
    for event in events:
        groups.setdefault((event.row_id, event.created_at), []).append(event)
    groups = list(groups.values())
    if len(events) >= CASE_TRACKERS_TIMELINE_BACKFILL_LIMIT and len(groups) > 1:
        groups.pop()
    return groups


def _timeline_backfill(case_id, tracker, user_id):
    """Write the past changes of a `changes` sync, oldest first, at most
    `CASE_TRACKERS_TIMELINE_BACKFILL_LIMIT` per call, and mark them
    `in_timeline` (those of items deleted from the case too, without an
    event). Each change names its author. The summary's `remaining` tells
    what is left for the next call."""
    by_row = _stored_pairs(tracker, _SourceCache(case_id))
    groups = _history_groups(case_trackers_db_events_not_in_timeline(
        tracker.id, list(by_row), CASE_TRACKERS_TIMELINE_BACKFILL_LIMIT))
    row_ids = sorted({group[0].row_id for group in groups})
    later = {}
    if groups:
        for event in case_trackers_db_value_events_after(row_ids, groups[0][0].id):
            later.setdefault(event.row_id, []).append(event)
    writer = CaseTrackerTimelineWriter(case_id, tracker, user_id, _utcnow(), {})
    for group in groups:
        for event in group:
            event.in_timeline = True
        stored, item = by_row[group[0].row_id]
        last_id = group[-1].id
        values = war_room_trackers_values_before(
            stored.values, [event for event in later.get(stored.id, ()) if event.id > last_id])
        writer.row_history(stored, item, [(event.action, event.details or {}) for event in group],
                           group[0].created_at, values, group[0].user_id)
    if not case_trackers_db_commit():
        raise BusinessProcessingError('The timeline was changed meanwhile, retry')
    writer.finish()
    summary = writer.summary()
    summary['remaining'] = case_trackers_db_events_not_in_timeline_count(tracker.id, list(by_row))
    return summary


def _manual_row(tracker, row_id):
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows of a mirrored tracker follow the case')
    rows = case_trackers_db_rows_by_ids(tracker.id, [row_id])
    if not rows:
        raise ObjectNotFoundError()
    return rows[0]


def case_trackers_rows_delete(case_id, tracker, row_id):
    row = _manual_row(tracker, row_id)
    ref = (row.item_type, row.label)
    case_trackers_db_delete(row)
    case_trackers_db_commit()
    track_activity(f'tracker "{tracker.name}": removed {war_room_trackers_describe_rows([ref])}', caseid=case_id)


def case_trackers_rows_reorder(tracker, ids):
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows of a mirrored tracker follow the case')
    war_room_trackers_reorder(case_trackers_db_rows(tracker.id), ids)
    case_trackers_db_commit()


# -------------------------------------------------------------- History --

def _serialize_events(events, manual):
    keys = case_trackers_db_rows_keys({event.row_id for event in events})
    names = war_room_trackers_db_user_names({event.user_id for event in events})
    out = []
    for event in events:
        item_type, item_id = keys.get(event.row_id, (None, None))
        out.append({
            'id': event.id,
            'row_id': event.row_id,
            'row_key': _row_key(item_type, item_id, event.row_id, manual),
            'action': event.action,
            'details': event.details,
            'user': _user_ref(event.user_id, names),
            'created_at': _isoformat(event.created_at),
        })
    return out


def case_trackers_history(tracker, row_id=None, item_type=None, item_id=None):
    """Events of one row (by `row_id` or item) or the latest of the
    tracker, newest first."""
    if row_id is not None:
        row_ids = [row_id]
    elif item_type is not None and item_id is not None:
        row_ids = [row.id for row in case_trackers_db_rows(tracker.id)
                   if row.item_type == item_type and row.item_id == item_id]
    else:
        row_ids = None
    return _serialize_events(case_trackers_db_events(tracker.id, row_ids=row_ids, limit=CASE_TRACKERS_HISTORY_LIMIT),
                             tracker.source == 'manual')
