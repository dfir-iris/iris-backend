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

"""War-room trackers: user-defined tabs of the war-room scope whose rows
carry war-room steps, a note and extra column values.

A tracker is either `manual` (rows added by hand, free text or war-room
items) or mirrors the room (`assets`, `iocs`, `customers`, `cases`,
`registry_assets`). A mirror's rows are the current source items, listed
through the war-room scope functions, merged with the stored state of
each item (`WarRoomTrackerRow`, created lazily on the first change). The
state of an item that left the room is kept, hidden, and comes back with
the item.

Authorization is NOT done here. The blueprint checks war-room access and
hands in a `WarRoomTrackerAccess`: the attached cases the caller can read
and its Assets Manager scope (None without asset_manager_read). Items
outside it are never listed; a manual row pointing at one is returned as
`restricted`, without the item.

A tracker can write its rows to the case or war-room timelines, see
`war_room_tracker_timelines`. The access then also holds the cases the
caller has full access to: only those get events.

IOCs are per case: the mirror groups the IOCs with the same value (as
the scope listing does, by `group_key`) into one row. The row is keyed
by the IOC holding the stored state, or by the lowest IOC id when none
does.
"""

import datetime
import math
import re

from app.business.war_room_board_widgets import TRACKER_WIDGET_TYPES
from app.business.war_room_board_widgets import WAR_ROOM_BOARD_LIST_ROWS
from app.business.war_room_board_widgets import war_room_board_widgets_prune
from app.business.war_room_managed_assets import war_room_managed_assets_list
from app.business.war_room_tracker_timelines import WarRoomTrackerTimelineWriter
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_check_columns
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_item_links
from app.business.war_room_tracker_timelines import war_room_tracker_timelines_validate
from app.business.war_room_scope import war_room_scope_list_assets
from app.business.war_room_scope import war_room_scope_list_customers
from app.business.war_room_scope import war_room_scope_list_iocs
from app.business.war_rooms import war_room_cases_list
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_add
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_asset_flags
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_commit
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_delete
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_events
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_events_not_in_timeline
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_events_not_in_timeline_count
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_events_not_in_timeline_counts
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_existing_user_ids
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_row_counts
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_row_step_remove
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_row_steps
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_rows
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_rows_by_ids
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_rows_keys
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_rows_max_sort
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_step_find_by_name
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_step_get
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_steps
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_tracker_get
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_tracker_lock
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_trackers
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_user_names
from app.datamgmt.war_rooms.war_room_trackers_db import war_room_trackers_db_value_events_after
from app.iris_engine.utils.tracker import track_activity
from app.models.assets import ASSET_FLAG_COLORS
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WAR_ROOM_TRACKER_ITEM_TYPES
from app.models.war_rooms import WAR_ROOM_TRACKER_SOURCES
from app.models.war_rooms import WarRoomTracker
from app.models.war_rooms import WarRoomTrackerRow
from app.models.war_rooms import WarRoomTrackerRowEvent
from app.models.war_rooms import WarRoomTrackerRowStep
from app.models.war_rooms import WarRoomTrackerStep


WAR_ROOM_TRACKERS_ROWS_LIMIT = 5000
WAR_ROOM_TRACKERS_MAX_STEPS = 50
WAR_ROOM_TRACKERS_MAX_TRACKERS = 50
WAR_ROOM_TRACKERS_MAX_COLUMNS = 20
WAR_ROOM_TRACKERS_MAX_BATCH = 500
WAR_ROOM_TRACKERS_HISTORY_LIMIT = 200
# Past changes written to the timelines per backfill call.
WAR_ROOM_TRACKERS_TIMELINE_BACKFILL_LIMIT = 2000

WAR_ROOM_TRACKER_COLUMN_TYPES = ('text', 'longtext', 'number', 'date', 'datetime', 'select', 'multiselect',
                                 'checkbox', 'user', 'url')

_STEP_NAME_MAX = 64
_TRACKER_NAME_MAX = 128
_DESCRIPTION_MAX = 4000
_COLUMN_LABEL_MAX = 64
_MAX_OPTIONS = 50
_OPTION_MAX = 64
_TEXT_MAX = 1024
_LONGTEXT_MAX = 20000
_URL_MAX = 2048
_LABEL_MAX = 512
_NOTE_MAX = 20000
_ICON_RE = re.compile(r'^[a-z0-9-]{1,64}$')
_COLUMN_KEY_RE = re.compile(r'^c_[a-z0-9_]{1,40}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)

_SOURCE_ITEM_TYPES = {
    'assets': 'asset',
    'iocs': 'ioc',
    'customers': 'customer',
    'cases': 'case',
    'registry_assets': 'managed_asset',
}
_ITEM_TYPE_SOURCES = {item_type: source for source, item_type in _SOURCE_ITEM_TYPES.items()}

_ITEM_NOT_IN_ROOM = 'is not in this war room'


class WarRoomTrackerAccess:
    """What the caller may see of the war room, decided by the blueprint:
    the attached cases it can read, its Assets Manager scope (None when it
    lacks asset_manager_read) and the readable cases it has full access to
    (where the timeline events go)."""

    def __init__(self, readable_case_ids, registry_scope=None, writable_case_ids=None):
        self.readable_case_ids = list(readable_case_ids or ())
        self.registry_scope = registry_scope
        self.writable_case_ids = set(writable_case_ids or ())


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


def war_room_trackers_validate_color(value):
    """Same palette as the asset flags."""
    if not isinstance(value, str) or value not in ASSET_FLAG_COLORS:
        raise BusinessProcessingError(f'color must be one of: {", ".join(ASSET_FLAG_COLORS)}')
    return value


def war_room_trackers_validate_icon(value):
    """A lucide icon name, as for the asset flags; empty clears it."""
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not _ICON_RE.fullmatch(value):
        raise BusinessProcessingError('icon must be lowercase letters, digits and dashes (max 64)')
    return value


def _validate_bool(value, field):
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def war_room_trackers_validate_source(value):
    if not isinstance(value, str) or value not in WAR_ROOM_TRACKER_SOURCES:
        raise BusinessProcessingError(f'source must be one of: {", ".join(WAR_ROOM_TRACKER_SOURCES)}')
    return value


def _validate_id_list(value, field, max_len=WAR_ROOM_TRACKERS_MAX_BATCH):
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


def _slug(label):
    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
    return slug[:24] or 'col'


def _column_key(label, taken):
    base = f'c_{_slug(label)}'
    key = base
    suffix = 2
    while key in taken:
        key = f'{base}_{suffix}'
        suffix += 1
    return key


def _validate_options(value, index):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError(f'Column #{index + 1}: options must be a non-empty list of strings')
    if len(value) > _MAX_OPTIONS:
        raise BusinessProcessingError(f'Column #{index + 1}: at most {_MAX_OPTIONS} options')
    out = []
    for option in value:
        if not isinstance(option, str) or not option.strip() or len(option.strip()) > _OPTION_MAX:
            raise BusinessProcessingError(
                f'Column #{index + 1}: options must be strings of 1 to {_OPTION_MAX} characters')
        if option.strip() not in out:
            out.append(option.strip())
    return out


def war_room_trackers_validate_columns(value):
    """Normalised list of extra columns. A column keeps its `key` when it
    has a valid one, otherwise a key is derived from its label."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise BusinessProcessingError('columns must be a list')
    if len(value) > WAR_ROOM_TRACKERS_MAX_COLUMNS:
        raise BusinessProcessingError(f'A tracker has at most {WAR_ROOM_TRACKERS_MAX_COLUMNS} columns')
    taken = set()
    for column in value:
        if isinstance(column, dict) and column.get('key') is not None:
            key = column['key']
            if not isinstance(key, str) or not _COLUMN_KEY_RE.fullmatch(key):
                raise BusinessProcessingError('A column key must match c_[a-z0-9_]{1,40}')
            if key in taken:
                raise BusinessProcessingError(f'Duplicated column key {key}')
            taken.add(key)
    out = []
    for index, column in enumerate(value):
        if not isinstance(column, dict):
            raise BusinessProcessingError('Each column must be an object')
        label = _validate_name(column.get('label'), f'Column #{index + 1} label', _COLUMN_LABEL_MAX)
        column_type = column.get('type')
        if column_type not in WAR_ROOM_TRACKER_COLUMN_TYPES:
            raise BusinessProcessingError(
                f'Column #{index + 1}: type must be one of: {", ".join(WAR_ROOM_TRACKER_COLUMN_TYPES)}')
        key = column.get('key')
        if key is None:
            key = _column_key(label, taken)
            taken.add(key)
        entry = {'key': key, 'label': label, 'type': column_type}
        if column_type in ('select', 'multiselect'):
            entry['options'] = _validate_options(column.get('options'), index)
        out.append(entry)
    return out


def _validate_date(value, _column):
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError()
    datetime.date.fromisoformat(value)
    return value


def _validate_datetime(value, _column):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError()
    raw = value.strip()
    if raw.endswith(('Z', 'z')):
        raw = f'{raw[:-1]}+00:00'
    datetime.datetime.fromisoformat(raw)
    return value.strip()


def _validate_text(value, _column):
    if not isinstance(value, str) or len(value) > _TEXT_MAX:
        raise ValueError()
    return value


def _validate_longtext(value, _column):
    if not isinstance(value, str) or len(value) > _LONGTEXT_MAX:
        raise ValueError()
    return value


def _validate_number(value, _column):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError()
    return value


def _validate_select(value, column):
    if not isinstance(value, str) or value not in column.get('options', ()):
        raise ValueError()
    return value


def _validate_multiselect(value, column):
    if not isinstance(value, list):
        raise ValueError()
    out = []
    for option in value:
        if not isinstance(option, str) or option not in column.get('options', ()):
            raise ValueError()
        if option not in out:
            out.append(option)
    return out


def _validate_checkbox(value, _column):
    if not isinstance(value, bool):
        raise ValueError()
    return value


def _validate_user(value, _column):
    if not _is_int(value) or value <= 0:
        raise ValueError()
    return value


def _validate_url(value, _column):
    if not isinstance(value, str) or len(value) > _URL_MAX or not _URL_RE.fullmatch(value.strip()):
        raise ValueError()
    return value.strip()


_VALUE_VALIDATORS = {
    'text': _validate_text,
    'longtext': _validate_longtext,
    'number': _validate_number,
    'date': _validate_date,
    'datetime': _validate_datetime,
    'select': _validate_select,
    'multiselect': _validate_multiselect,
    'checkbox': _validate_checkbox,
    'user': _validate_user,
    'url': _validate_url,
}

_VALUE_HINTS = {
    'text': f'a string of at most {_TEXT_MAX} characters',
    'longtext': f'a string of at most {_LONGTEXT_MAX} characters',
    'number': 'a number',
    'date': 'a YYYY-MM-DD date',
    'datetime': 'an ISO 8601 date-time',
    'select': 'one of the column options',
    'multiselect': 'a list of the column options',
    'checkbox': 'a boolean',
    'user': 'a user id',
    'url': 'an http(s) URL',
}


def war_room_trackers_validate_value(column, value):
    """Normalised value for `column`; None clears it."""
    if value is None:
        return None
    try:
        return _VALUE_VALIDATORS[column['type']](value, column)
    except (ValueError, TypeError):
        raise BusinessProcessingError(f'{column["label"]} must be {_VALUE_HINTS[column["type"]]}')


def war_room_trackers_validate_values(columns, values):
    """`{key: value|None}` validated against the tracker columns. Unknown
    keys are refused, users must exist."""
    if not isinstance(values, dict):
        raise BusinessProcessingError('values must be an object')
    by_key = {column['key']: column for column in columns or ()}
    out = {}
    user_ids = set()
    for key, value in values.items():
        column = by_key.get(key)
        if column is None:
            raise BusinessProcessingError(f'Unknown column {key}')
        out[key] = war_room_trackers_validate_value(column, value)
        if column['type'] == 'user' and out[key] is not None:
            user_ids.add(out[key])
    if user_ids:
        missing = user_ids - war_room_trackers_db_existing_user_ids(user_ids)
        if missing:
            raise BusinessProcessingError(f'Unknown user #{min(missing)}')
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

def war_room_trackers_serialize_step(step):
    return {
        'id': step.id,
        'name': step.name,
        'description': step.description,
        'color': step.color,
        'icon': step.icon,
        'sort_order': step.sort_order,
    }


def war_room_trackers_steps_list(war_room_id):
    return war_room_trackers_db_steps(war_room_id)


def _step_get(war_room_id, step_id):
    step = war_room_trackers_db_step_get(war_room_id, step_id)
    if step is None:
        raise ObjectNotFoundError()
    return step


def _validate_step_name(war_room_id, value, exclude_id=None):
    name = _validate_name(value, 'Step name', _STEP_NAME_MAX)
    if war_room_trackers_db_step_find_by_name(war_room_id, name, exclude_id=exclude_id) is not None:
        raise BusinessProcessingError(f'A step named "{name}" already exists')
    return name


def war_room_trackers_build_step(war_room_id, data, sort_order):
    """An unsaved step from a create payload (validated)."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    color = data.get('color')
    return WarRoomTrackerStep(
        war_room_id=war_room_id,
        name=_validate_step_name(war_room_id, data.get('name')),
        description=_validate_description(data.get('description')),
        color=war_room_trackers_validate_color(color) if color is not None else 'slate',
        icon=war_room_trackers_validate_icon(data.get('icon')),
        sort_order=sort_order,
    )


def _next_sort(objects):
    return max((obj.sort_order for obj in objects), default=-1) + 1


def war_room_trackers_step_create(war_room_id, data):
    steps = war_room_trackers_db_steps(war_room_id)
    if len(steps) >= WAR_ROOM_TRACKERS_MAX_STEPS:
        raise BusinessProcessingError(f'A war room has at most {WAR_ROOM_TRACKERS_MAX_STEPS} steps')
    step = war_room_trackers_db_add(war_room_trackers_build_step(war_room_id, data, _next_sort(steps)))
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('A step with this name was created meanwhile')
    return step


def war_room_trackers_step_update(war_room_id, step_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    step = _step_get(war_room_id, step_id)
    if 'name' in data:
        step.name = _validate_step_name(war_room_id, data['name'], exclude_id=step.id)
    if 'description' in data:
        step.description = _validate_description(data['description'])
    if 'color' in data:
        step.color = war_room_trackers_validate_color(data['color'])
    if 'icon' in data:
        step.icon = war_room_trackers_validate_icon(data['icon'])
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('A step with this name was created meanwhile')
    return step


def war_room_trackers_step_delete(war_room_id, step_id):
    """Delete a step: the rows lose it (FK cascade) and it is pruned from
    the steps offered by the trackers and from the board widgets."""
    step = _step_get(war_room_id, step_id)
    for tracker in war_room_trackers_db_trackers(war_room_id):
        if tracker.step_ids and step.id in tracker.step_ids:
            tracker.step_ids = [sid for sid in tracker.step_ids if sid != step.id]
    war_room_board_widgets_prune(war_room_id, step_id=step.id)
    name = step.name
    war_room_trackers_db_delete(step)
    war_room_trackers_db_commit()
    return name


def war_room_trackers_reorder(objects, ids):
    """Give `objects` the order of `ids` (each one of them, once); the
    others keep their relative order after them."""
    ids = _validate_id_list(ids, 'ids', max_len=WAR_ROOM_TRACKERS_MAX_BATCH)
    by_id = {obj.id: obj for obj in objects}
    unknown = [obj_id for obj_id in ids if obj_id not in by_id]
    if unknown:
        raise BusinessProcessingError(f'Unknown id #{unknown[0]}')
    ordered = [by_id[obj_id] for obj_id in ids]
    ordered += [obj for obj in objects if obj.id not in set(ids)]
    for index, obj in enumerate(ordered):
        obj.sort_order = index
    return ordered


def war_room_trackers_steps_reorder(war_room_id, ids):
    ordered = war_room_trackers_reorder(war_room_trackers_db_steps(war_room_id), ids)
    war_room_trackers_db_commit()
    return ordered


# -------------------------------------------------------------- Sources --

def _asset_flag_entries(flags, flag_meta):
    out = []
    for flag in flags or ():
        name, color, icon = flag_meta.get(flag['flag_id'], (None, None, None))
        out.append({'flag_id': flag['flag_id'], 'name': name, 'color': color, 'icon': icon,
                    'reason': flag.get('reason'), 'set_at': flag.get('set_at')})
    return out


def _item(item_type, item_id, label, summary, member_ids=None, timeline=None):
    """A source item. `timeline` is what it brings to the timelines (see
    `war_room_tracker_timelines_item_links`); never sent to the client."""
    return {'item_type': item_type, 'item_id': item_id, 'label': label, 'item': summary,
            'member_ids': tuple(member_ids or (item_id,)),
            'timeline': timeline or war_room_tracker_timelines_item_links()}


def _source_assets(war_room_id, access):
    if not access.readable_case_ids:
        return [], False
    result = war_room_scope_list_assets(access.readable_case_ids, include_vulnerabilities=False)
    flag_meta = war_room_trackers_db_asset_flags()
    items = []
    for asset in result['data']:
        items.append(_item('asset', asset['asset_id'], asset['asset_name'], {
            'asset_id': asset['asset_id'],
            'asset_name': asset['asset_name'],
            'asset_type': asset.get('asset_type_name'),
            'case_id': asset['case_id'],
            'case_name': asset['case_name'],
            'customer_id': asset['customer_id'],
            'customer_name': asset['customer_name'],
            'compromise_status_id': asset.get('asset_compromise_status_id'),
            'analysis_status_id': asset.get('analysis_status_id'),
            'flags': _asset_flag_entries(asset.get('flags'), flag_meta),
        }, timeline=war_room_tracker_timelines_item_links(
            case_links=[(asset['case_id'], (asset['asset_id'],), ())], visibility_case_ids=[asset['case_id']])))
    return items, bool(result.get('truncated'))


def war_room_trackers_group_iocs(rows):
    """One item per indicator (`group_key`), keyed by its lowest IOC id,
    with every case it is seen in."""
    groups = {}
    for row in rows:
        group = groups.get(row['group_key'])
        if group is None:
            group = {'ioc_ids': [], 'ioc_value': row['ioc_value'], 'ioc_type': row.get('ioc_type_name'),
                     'case_ids': [], 'case_names': [], 'case_iocs': {}}
            groups[row['group_key']] = group
        group['ioc_ids'].append(row['ioc_id'])
        group['case_iocs'].setdefault(row['case_id'], []).append(row['ioc_id'])
        if row['case_id'] not in group['case_ids']:
            group['case_ids'].append(row['case_id'])
            group['case_names'].append(row['case_name'])
    items = []
    for group in groups.values():
        ioc_ids = sorted(group['ioc_ids'])
        items.append(_item('ioc', ioc_ids[0], group['ioc_value'], {
            'ioc_id': ioc_ids[0],
            'ioc_ids': ioc_ids,
            'ioc_value': group['ioc_value'],
            'ioc_type': group['ioc_type'],
            'case_ids': group['case_ids'],
            'case_names': group['case_names'],
        }, member_ids=ioc_ids, timeline=war_room_tracker_timelines_item_links(
            case_links=[(case_id, (), sorted(ids)) for case_id, ids in group['case_iocs'].items()],
            visibility_case_ids=group['case_ids'])))
    return items


def _source_iocs(war_room_id, access):
    if not access.readable_case_ids:
        return [], False
    result = war_room_scope_list_iocs(access.readable_case_ids)
    return war_room_trackers_group_iocs(result['data']), bool(result.get('truncated'))


def _source_customers(war_room_id, access):
    if not access.readable_case_ids:
        return [], False
    result = war_room_scope_list_customers(access.readable_case_ids, include_vulnerabilities=False)
    items = []
    for customer in result['data']:
        if customer['customer_id'] is None:
            continue
        items.append(_item('customer', customer['customer_id'], customer['customer_name'], {
            'customer_id': customer['customer_id'],
            'customer_name': customer['customer_name'],
            'case_count': len(customer.get('cases') or ()),
        }, timeline=war_room_tracker_timelines_item_links(
            case_links=[(case['case_id'], (), ()) for case in customer.get('cases') or ()],
            visibility_case_ids=[case['case_id'] for case in customer.get('cases') or ()])))
    return items, False


def _source_cases(war_room_id, access):
    readable = set(access.readable_case_ids)
    if not readable:
        return [], False
    items = []
    for row in war_room_cases_list(war_room_id):
        if row.case_id not in readable:
            continue
        items.append(_item('case', row.case_id, row.case_name, {
            'case_id': row.case_id,
            'case_name': row.case_name,
            'customer_id': getattr(row, 'customer_id', None),
            'customer_name': getattr(row, 'customer_name', None),
            'state': getattr(row, 'state_name', None),
            'closed': getattr(row, 'close_date', None) is not None,
        }, timeline=war_room_tracker_timelines_item_links(case_links=[(row.case_id, (), ())],
                                                          visibility_case_ids=[row.case_id])))
    return items, False


def _source_registry_assets(war_room_id, access):
    if access.registry_scope is None:
        return [], False
    result = war_room_managed_assets_list(war_room_id, access.registry_scope, access.readable_case_ids, False)
    by_id = {}
    for attachment in result['data']:
        entry = by_id.get(attachment['managed_asset_id'])
        if entry is None:
            entry = {
                'managed_asset_id': attachment['managed_asset_id'],
                'name': attachment['asset_name'],
                'type': attachment.get('asset_type_name'),
                'ip': attachment.get('asset_ip'),
                'domain': attachment.get('asset_domain'),
                'criticality': attachment.get('criticality'),
                'environment': attachment.get('environment'),
                'owner': attachment.get('owner'),
                'is_active': attachment.get('is_active'),
                'customer_id': attachment.get('customer_id'),
                'customer_name': attachment.get('customer_name'),
                'case_ids': [],
                'case_names': [],
            }
            by_id[attachment['managed_asset_id']] = entry
        if attachment.get('case_id') is not None and attachment['case_id'] not in entry['case_ids']:
            entry['case_ids'].append(attachment['case_id'])
            entry['case_names'].append(attachment.get('case_name'))
    items = [_item('managed_asset', entry['managed_asset_id'], entry['name'], entry,
                   timeline=war_room_tracker_timelines_item_links(
                       case_links=[(case_id, (), ()) for case_id in entry['case_ids']],
                       managed_asset_id=entry['managed_asset_id']))
             for entry in sorted(by_id.values(), key=lambda e: ((e['name'] or '').lower(), e['managed_asset_id']))]
    return items, bool(result.get('truncated'))


_SOURCE_LISTERS = {
    'assets': _source_assets,
    'iocs': _source_iocs,
    'customers': _source_customers,
    'cases': _source_cases,
    'registry_assets': _source_registry_assets,
}


def war_room_trackers_source_items(war_room_id, source, access):
    """`(items, truncated)`: the current items of a mirrored source the
    caller can see."""
    return _SOURCE_LISTERS[source](war_room_id, access)


class _SourceCache:
    """The source items, listed at most once per request."""

    def __init__(self, war_room_id, access):
        self._war_room_id = war_room_id
        self._access = access
        self._items = {}

    def items(self, source):
        if source not in self._items:
            self._items[source] = war_room_trackers_source_items(self._war_room_id, source, self._access)
        return self._items[source]

    def index(self, item_type):
        """`{member id: item}` of the items of `item_type`."""
        items, _ = self.items(_ITEM_TYPE_SOURCES[item_type])
        return {member_id: item for item in items for member_id in item['member_ids']}


# ------------------------------------------------------------- Trackers --

def _validate_step_ids(value, room_step_ids):
    if value is None:
        return None
    step_ids = _validate_id_list(value, 'step_ids', max_len=WAR_ROOM_TRACKERS_MAX_STEPS)
    unknown = [step_id for step_id in step_ids if step_id not in room_step_ids]
    if unknown:
        raise BusinessProcessingError(f'Unknown step #{unknown[0]}')
    return step_ids


def war_room_trackers_build_tracker(war_room_id, data, room_step_ids, sort_order, user_id):
    """An unsaved tracker from a create payload (validated)."""
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    show_asset_flags = data.get('show_asset_flags')
    columns = war_room_trackers_validate_columns(data.get('columns'))
    now = _utcnow()
    return WarRoomTracker(
        war_room_id=war_room_id,
        name=_validate_name(data.get('name'), 'Tracker name', _TRACKER_NAME_MAX),
        source=war_room_trackers_validate_source(data.get('source')),
        icon=war_room_trackers_validate_icon(data.get('icon')),
        description=_validate_description(data.get('description')),
        step_ids=_validate_step_ids(data.get('step_ids'), room_step_ids),
        columns=columns,
        show_asset_flags=_validate_bool(show_asset_flags, 'show_asset_flags') if show_asset_flags is not None
        else False,
        timeline_sync=war_room_tracker_timelines_validate(data.get('timeline_sync'), columns, war_room_id),
        sort_order=sort_order,
        created_by_id=user_id,
        created_at=now,
        updated_at=now,
    )


def war_room_trackers_serialize(tracker, row_count, names, timeline_backfill=0):
    """`timeline_backfill`: with a `changes` timeline sync, the past
    changes of the rows the caller sees not in the timelines yet (made
    before the sync was on)."""
    return {
        'id': tracker.id,
        'war_room_id': tracker.war_room_id,
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


def _visible_stored_rows(tracker, cache):
    """`{row_id: (stored, item)}`: the stored rows of a tracker the caller
    sees (free-text rows, and rows whose item is in its sight)."""
    if tracker.source == 'manual':
        pairs = _manual_pairs(war_room_trackers_db_rows(tracker.id), cache)
    else:
        pairs, _ = _mirror_pairs(tracker, cache)
    return {stored.id: (stored, item) for stored, item in pairs
            if stored is not None and (stored.item_type is None or item is not None)}


def war_room_trackers_serialize_many(war_room_id, trackers, access):
    """Trackers with the number of rows the caller sees and, for a
    `changes` timeline sync, the past changes of those rows it can still
    write to the timelines."""
    cache = _SourceCache(war_room_id, access)
    stored = war_room_trackers_db_row_counts([t.id for t in trackers if t.source == 'manual'])
    names = war_room_trackers_db_user_names([t.created_by_id for t in trackers])
    pending = war_room_trackers_db_events_not_in_timeline_counts([t.id for t in trackers if _writes_changes(t)])
    out = []
    for tracker in trackers:
        if tracker.source == 'manual':
            count = stored.get(tracker.id, 0)
        else:
            count = len(cache.items(tracker.source)[0])
        backfill = 0
        if pending.get(tracker.id):
            backfill = war_room_trackers_db_events_not_in_timeline_count(
                tracker.id, list(_visible_stored_rows(tracker, cache)))
        out.append(war_room_trackers_serialize(tracker, min(count, WAR_ROOM_TRACKERS_ROWS_LIMIT), names, backfill))
    return out


def war_room_trackers_list(war_room_id):
    return war_room_trackers_db_trackers(war_room_id)


def war_room_trackers_get(war_room_id, tracker_id):
    tracker = war_room_trackers_db_tracker_get(war_room_id, tracker_id)
    if tracker is None:
        raise ObjectNotFoundError()
    return tracker


def war_room_trackers_create(war_room_id, data, user_id):
    trackers = war_room_trackers_db_trackers(war_room_id)
    if len(trackers) >= WAR_ROOM_TRACKERS_MAX_TRACKERS:
        raise BusinessProcessingError(f'A war room has at most {WAR_ROOM_TRACKERS_MAX_TRACKERS} trackers')
    room_step_ids = {step.id for step in war_room_trackers_db_steps(war_room_id)}
    tracker = war_room_trackers_build_tracker(war_room_id, data, room_step_ids, _next_sort(trackers), user_id)
    war_room_trackers_db_add(tracker)
    war_room_trackers_db_commit()
    track_activity(f'created tracker "{tracker.name}"', war_room_id=war_room_id)
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


def war_room_trackers_update(war_room_id, tracker_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    tracker = war_room_trackers_get(war_room_id, tracker_id)
    if 'source' in data and data['source'] != tracker.source:
        raise BusinessProcessingError('The source of a tracker cannot be changed')
    if 'name' in data:
        tracker.name = _validate_name(data['name'], 'Tracker name', _TRACKER_NAME_MAX)
    if 'icon' in data:
        tracker.icon = war_room_trackers_validate_icon(data['icon'])
    if 'description' in data:
        tracker.description = _validate_description(data['description'])
    if 'step_ids' in data:
        room_step_ids = {step.id for step in war_room_trackers_db_steps(war_room_id)}
        tracker.step_ids = _validate_step_ids(data['step_ids'], room_step_ids)
    if 'columns' in data:
        columns = data['columns']
        if isinstance(columns, list):
            columns = _merge_column_keys(tracker.columns, columns)
        tracker.columns = war_room_trackers_validate_columns(columns)
    if 'show_asset_flags' in data:
        tracker.show_asset_flags = _validate_bool(data['show_asset_flags'], 'show_asset_flags')
    if 'timeline_sync' in data:
        tracker.timeline_sync = war_room_tracker_timelines_validate(data['timeline_sync'], tracker.columns,
                                                                    war_room_id)
    elif 'columns' in data:
        war_room_tracker_timelines_check_columns(tracker.timeline_sync, tracker.columns)
    if 'columns' in data:
        war_room_board_widgets_prune(war_room_id, tracker_columns=(tracker.id, tracker.columns))
    tracker.updated_at = _utcnow()
    war_room_trackers_db_commit()
    return tracker


def war_room_trackers_delete(war_room_id, tracker_id):
    tracker = war_room_trackers_get(war_room_id, tracker_id)
    name = tracker.name
    war_room_board_widgets_prune(war_room_id, tracker_id=tracker.id)
    war_room_trackers_db_delete(tracker)
    war_room_trackers_db_commit()
    track_activity(f'deleted tracker "{name}"', war_room_id=war_room_id)
    return name


def war_room_trackers_trackers_reorder(war_room_id, ids):
    ordered = war_room_trackers_reorder(war_room_trackers_db_trackers(war_room_id), ids)
    war_room_trackers_db_commit()
    return ordered


# ----------------------------------------------------------------- Rows --

def _row_key(item_type, item_id, row_id, manual):
    """`row:<id>` for the rows of a manual tracker (an item can have
    several), `<item_type>:<item_id>` for the items of a mirror (the same
    before and after their row is stored)."""
    if item_type is not None and not manual:
        return f'{item_type}:{item_id}'
    return f'row:{row_id}'


def _custom_label(stored):
    """The custom label of an item row, None for free text (its `label`
    is the row itself)."""
    if stored is None or stored.item_type is None:
        return None
    return stored.label


def _row_payload(stored, item, steps, names, manual):
    """A row: `stored` is the stored state (None for a mirrored item not
    touched yet), `item` the source item (None for free text, or for an
    item the caller cannot see). `label` is the item's own label for an
    item row, `custom_label` the one given to the row."""
    if stored is None:
        return {
            'row_id': None, 'key': _row_key(item['item_type'], item['item_id'], None, manual),
            'item_type': item['item_type'], 'item_id': item['item_id'], 'label': item['label'],
            'custom_label': None, 'item': item['item'], 'restricted': False, 'steps': [], 'note': None,
            'values': {}, 'updated_at': None, 'updated_by': None,
        }
    restricted = stored.item_type is not None and item is None
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
        'custom_label': _custom_label(stored),
        'item': item['item'] if item is not None else None,
        'restricted': restricted,
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
    for step in war_room_trackers_db_row_steps(stored_ids):
        steps.setdefault(step.row_id, []).append(step)
        user_ids.add(step.set_by_id)
    names = war_room_trackers_db_user_names(user_ids)
    return [_row_payload(stored, item, steps, names, manual) for stored, item in pairs]


def war_room_trackers_match_stored(item, stored_by_item):
    """The stored row of a source item: the one of its lowest member
    (an IOC group has several) holding some state."""
    for member_id in sorted(item['member_ids']):
        stored = stored_by_item.get((item['item_type'], member_id))
        if stored is not None:
            return stored
    return None


def _mirror_pairs(tracker, cache):
    items, truncated = cache.items(tracker.source)
    stored_by_item = {(row.item_type, row.item_id): row for row in war_room_trackers_db_rows(tracker.id)
                      if row.item_type is not None}
    pairs = [(war_room_trackers_match_stored(item, stored_by_item), item) for item in items]
    return pairs, truncated


def _manual_pairs(stored_rows, cache):
    types = {row.item_type for row in stored_rows if row.item_type is not None}
    indexes = {item_type: cache.index(item_type) for item_type in types}
    return [(row, indexes[row.item_type].get(row.item_id) if row.item_type is not None else None)
            for row in stored_rows]


def _tracker_pairs(tracker, cache):
    """`(pairs, truncated)`: the `(stored, item)` rows of a tracker, at
    most `WAR_ROOM_TRACKERS_ROWS_LIMIT`."""
    limit = WAR_ROOM_TRACKERS_ROWS_LIMIT
    if tracker.source == 'manual':
        stored = war_room_trackers_db_rows(tracker.id, limit=limit)
        return _manual_pairs(stored[:limit], cache), len(stored) > limit
    pairs, truncated = _mirror_pairs(tracker, cache)
    return pairs[:limit], truncated or len(pairs) > limit


def war_room_trackers_rows_list(war_room_id, tracker, access):
    """`{data, truncated, limit}`: a mirror lists every current source
    item merged with its stored state, a manual tracker its stored rows."""
    pairs, truncated = _tracker_pairs(tracker, _SourceCache(war_room_id, access))
    return {'data': _rows_payload(pairs, tracker.source == 'manual'), 'truncated': truncated,
            'limit': WAR_ROOM_TRACKERS_ROWS_LIMIT}


def _parse_steps_for_write(value, field, offered_step_ids):
    step_ids = _validate_id_list(value, field, max_len=WAR_ROOM_TRACKERS_MAX_STEPS)
    unknown = [step_id for step_id in step_ids if step_id not in offered_step_ids]
    if unknown:
        raise BusinessProcessingError(f'Step #{unknown[0]} is not offered by this tracker')
    return step_ids


def _offered_step_ids(tracker, room_steps):
    room_step_ids = [step.id for step in room_steps]
    if tracker.step_ids is None:
        return set(room_step_ids)
    return set(tracker.step_ids) & set(room_step_ids)


def war_room_trackers_parse_state_changes(body, tracker, room_steps):
    """The changes of a bulk state update, validated. Keys absent from
    the body are left out of the result."""
    offered = _offered_step_ids(tracker, room_steps)
    changes = {}
    if body.get('set_step_ids') is not None:
        if body.get('add_step_ids') or body.get('remove_step_ids'):
            raise BusinessProcessingError('set_step_ids cannot be combined with add_step_ids / remove_step_ids')
        changes['set_step_ids'] = _parse_steps_for_write(body['set_step_ids'], 'set_step_ids', offered)
    if body.get('add_step_ids') is not None:
        changes['add_step_ids'] = _parse_steps_for_write(body['add_step_ids'], 'add_step_ids', offered)
    if body.get('remove_step_ids') is not None:
        changes['remove_step_ids'] = _validate_id_list(body['remove_step_ids'], 'remove_step_ids',
                                                       max_len=WAR_ROOM_TRACKERS_MAX_STEPS)
    if 'note' in body:
        changes['note'] = _validate_note(body['note'])
    if body.get('values') is not None:
        changes['values'] = war_room_trackers_validate_values(tracker.columns, body['values'])
    if 'label' in body:
        changes['label'] = war_room_trackers_parse_label(body['label'])
    return changes


def war_room_trackers_parse_label(value):
    """A new label: None (or blank) drops the custom label of an item
    row, a free-text row cannot go without one."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _validate_label(value)


def war_room_trackers_apply_state(row, current_step_ids, changes, columns, step_names):
    """Apply `changes` to `row` (mutated in place). Returns `(to_add,
    to_remove, events)` where events are `(action, details)` of the
    actual changes."""
    events = []
    current = set(current_step_ids)
    if 'set_step_ids' in changes:
        target = set(changes['set_step_ids'])
        to_add = [step_id for step_id in changes['set_step_ids'] if step_id not in current]
        to_remove = sorted(current - target)
    else:
        to_add = [step_id for step_id in changes.get('add_step_ids', ()) if step_id not in current]
        to_remove = [step_id for step_id in changes.get('remove_step_ids', ()) if step_id in current]
    for step_id in to_add:
        events.append(('step_added', {'step_id': step_id, 'step_name': step_names.get(step_id)}))
    for step_id in to_remove:
        events.append(('step_removed', {'step_id': step_id, 'step_name': step_names.get(step_id)}))
    if 'note' in changes and changes['note'] != row.note:
        events.append(('note_changed', {'old': row.note, 'new': changes['note']}))
        row.note = changes['note']
    if 'label' in changes:
        if row.item_type is None and changes['label'] is None:
            raise BusinessProcessingError('label is required')
        if changes['label'] != row.label:
            events.append(('label_changed', {'old': row.label, 'new': changes['label']}))
            row.label = changes['label']
    if changes.get('values'):
        labels = {column['key']: column['label'] for column in columns or ()}
        values = dict(row.values or {})
        for key, value in changes['values'].items():
            old = values.get(key)
            if value is None:
                if key not in values:
                    continue
                del values[key]
            elif old == value and key in values:
                continue
            else:
                values[key] = value
            events.append(('value_changed', {'column_key': key, 'column_label': labels.get(key),
                                             'old': old, 'new': value}))
        row.values = values
    return to_add, to_remove, events


def _parse_row_refs(value):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError('rows must be a non-empty list')
    if len(value) > WAR_ROOM_TRACKERS_MAX_BATCH:
        raise BusinessProcessingError(f'At most {WAR_ROOM_TRACKERS_MAX_BATCH} rows at once')
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
        if item_type not in WAR_ROOM_TRACKER_ITEM_TYPES or not _is_int(item_id) or item_id <= 0:
            raise BusinessProcessingError('Each row needs a row_id, or an item_type and an item_id')
        refs.append((item_type, item_id))
    return refs


def _resolve_mirror_targets(tracker, refs, cache):
    """`[(stored or None, item)]` of the refs; every one must be a current,
    visible source item."""
    items, _ = cache.items(tracker.source)
    item_type = _SOURCE_ITEM_TYPES[tracker.source]
    by_member = {member_id: item for item in items for member_id in item['member_ids']}
    stored_rows = [row for row in war_room_trackers_db_rows(tracker.id) if row.item_type is not None]
    stored_by_item = {(row.item_type, row.item_id): row for row in stored_rows}
    stored_by_id = {row.id: row for row in stored_rows}
    targets = []
    seen = set()
    for kind, ref_id in refs:
        if kind == 'row':
            stored = stored_by_id.get(ref_id)
            if stored is None:
                raise BusinessProcessingError(f'Row #{ref_id} not found in this tracker')
            item = by_member.get(stored.item_id)
        elif kind == item_type:
            item = by_member.get(ref_id)
        else:
            raise BusinessProcessingError(f'This tracker lists {item_type} items only')
        if item is None:
            raise BusinessProcessingError(f'{kind.capitalize()} #{ref_id} {_ITEM_NOT_IN_ROOM}')
        key = (item['item_type'], item['item_id'])
        if key in seen:
            continue
        seen.add(key)
        targets.append((war_room_trackers_match_stored(item, stored_by_item), item))
    return targets


def _resolve_manual_targets(tracker, refs, cache):
    """The stored rows of the refs; an item ref targets every row of the
    item."""
    stored_rows = war_room_trackers_db_rows(tracker.id)
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
        war_room_trackers_db_add(WarRoomTrackerRowEvent(tracker_id=tracker_id, row_id=row_id, user_id=user_id,
                                                        created_at=now, action=action, details=details,
                                                        in_timeline=in_timeline))


def war_room_trackers_rows_update_state(war_room_id, tracker, body, access, user_id):
    """Bulk update of the steps / note / values / label of rows. Mirrored
    items get their stored row on their first actual change. One event
    per actual change. Returns `({data: [Row]}, changes)` (the targeted
    rows, and the `(item_type, label, events)` of the rows actually
    changed, see `war_room_trackers_describe_changes`). With a timeline
    sync, the result also holds the `timeline` summary of
    `WarRoomTrackerTimelineWriter.summary`."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid request')
    refs = _parse_row_refs(body.get('rows'))
    room_steps = war_room_trackers_db_steps(war_room_id)
    changes = war_room_trackers_parse_state_changes(body, tracker, room_steps)
    cache = _SourceCache(war_room_id, access)
    if tracker.source == 'manual':
        targets = _resolve_manual_targets(tracker, refs, cache)
    else:
        war_room_trackers_db_tracker_lock(tracker.id)
        targets = _resolve_mirror_targets(tracker, refs, cache)

    step_names = {step.id: step.name for step in room_steps}
    current_steps = {}
    for step in war_room_trackers_db_row_steps([stored.id for stored, _ in targets if stored is not None]):
        current_steps.setdefault(step.row_id, set()).add(step.step_id)
    now = _utcnow()
    writer = WarRoomTrackerTimelineWriter(war_room_id, tracker, access.writable_case_ids, user_id, now, step_names)
    writer.prefetch([stored.id for stored, _ in targets if stored is not None])
    result = []
    applied = []
    for stored, item in targets:
        row = stored
        if row is None:
            row = WarRoomTrackerRow(tracker_id=tracker.id, item_type=item['item_type'], item_id=item['item_id'],
                                    note=None, values={}, sort_order=0, created_by_id=user_id, created_at=now)
        to_add, to_remove, events = war_room_trackers_apply_state(
            row, current_steps.get(row.id, ()) if stored is not None else (), changes, tracker.columns, step_names)
        if events:
            row.updated_at = now
            row.updated_by_id = user_id
            if stored is None:
                war_room_trackers_db_add(row)
            for step_id in to_remove:
                war_room_trackers_db_row_step_remove(row.id, step_id)
            for step_id in to_add:
                war_room_trackers_db_add(WarRoomTrackerRowStep(row_id=row.id, step_id=step_id, set_by_id=user_id,
                                                               set_at=now))
            _record(tracker.id, row.id, user_id, now, events, writer.mode == 'changes')
            applied.append((row.item_type, row.label, events))
            step_ids = (set(current_steps.get(row.id, ())) - set(to_remove)) | set(to_add)
            writer.row_changed(row, item, events, step_ids)
        result.append((row if row.id is not None else None, item))
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('These rows were changed meanwhile, reload and retry')
    writer.finish()
    return _with_timeline({'data': _rows_payload(result, tracker.source == 'manual')}, writer), applied


def _parse_new_rows(value):
    if not isinstance(value, list) or not value:
        raise BusinessProcessingError('rows must be a non-empty list')
    if len(value) > WAR_ROOM_TRACKERS_MAX_BATCH:
        raise BusinessProcessingError(f'At most {WAR_ROOM_TRACKERS_MAX_BATCH} rows at once')
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            raise BusinessProcessingError('Each row must be an object')
        if entry.get('item_type') is not None:
            item_type = entry['item_type']
            item_id = entry.get('item_id')
            if item_type not in WAR_ROOM_TRACKER_ITEM_TYPES:
                raise BusinessProcessingError(f'item_type must be one of: {", ".join(WAR_ROOM_TRACKER_ITEM_TYPES)}')
            if not _is_int(item_id) or item_id <= 0:
                raise BusinessProcessingError('item_id must be an integer')
            out.append((item_type, item_id, war_room_trackers_parse_label(entry.get('label'))))
        else:
            out.append((None, None, _validate_label(entry.get('label'))))
    return out


def war_room_trackers_rows_add(war_room_id, tracker, body, access, user_id):
    """Add rows to a manual tracker: free text (`label`) or war-room items
    the caller can see (with an optional custom `label`). Items already
    in the tracker are skipped, unless `allow_duplicates`. Returns
    `{data: [Row]}` (the created rows)."""
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows can only be added to a manual tracker')
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid request')
    entries = _parse_new_rows(body.get('rows'))
    room_steps = war_room_trackers_db_steps(war_room_id)
    step_ids = []
    if body.get('step_ids') is not None:
        step_ids = _parse_steps_for_write(body['step_ids'], 'step_ids', _offered_step_ids(tracker, room_steps))
    note = _validate_note(body.get('note'))
    allow_duplicates = _validate_bool(body.get('allow_duplicates', False), 'allow_duplicates')

    cache = _SourceCache(war_room_id, access)
    indexes = {}
    for item_type, item_id, _ in entries:
        if item_type is None:
            continue
        if item_type not in indexes:
            indexes[item_type] = cache.index(item_type)
        if item_id not in indexes[item_type]:
            raise BusinessProcessingError(f'{item_type.capitalize()} #{item_id} {_ITEM_NOT_IN_ROOM}')

    existing = war_room_trackers_db_rows(tracker.id)
    if len(existing) + len(entries) > WAR_ROOM_TRACKERS_ROWS_LIMIT:
        raise BusinessProcessingError(f'A tracker holds at most {WAR_ROOM_TRACKERS_ROWS_LIMIT} rows')
    present = {(row.item_type, row.item_id) for row in existing if row.item_type is not None}
    step_names = {step.id: step.name for step in room_steps}
    sort_order = war_room_trackers_db_rows_max_sort(tracker.id) + 1
    now = _utcnow()
    writer = WarRoomTrackerTimelineWriter(war_room_id, tracker, access.writable_case_ids, user_id, now, step_names)
    created = []
    for item_type, item_id, label in entries:
        if item_type is not None and not allow_duplicates:
            if (item_type, item_id) in present:
                continue
            present.add((item_type, item_id))
        row = war_room_trackers_db_add(WarRoomTrackerRow(
            tracker_id=tracker.id, item_type=item_type, item_id=item_id, label=label, note=note, values={},
            sort_order=sort_order, created_by_id=user_id, created_at=now, updated_by_id=user_id, updated_at=now,
        ))
        sort_order += 1
        events = [('created', {'label': label} if label is not None else {})]
        for step_id in step_ids:
            war_room_trackers_db_add(WarRoomTrackerRowStep(row_id=row.id, step_id=step_id, set_by_id=user_id,
                                                           set_at=now))
            events.append(('step_added', {'step_id': step_id, 'step_name': step_names.get(step_id)}))
        _record(tracker.id, row.id, user_id, now, events, writer.mode == 'changes')
        item = indexes[item_type].get(item_id) if item_type is not None else None
        writer.row_changed(row, item, events, set(step_ids))
        created.append((row, item))
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('Some of these items were added meanwhile, reload and retry')
    writer.finish()
    return _with_timeline({'data': _rows_payload(created, True)}, writer)


def _with_timeline(result, writer):
    summary = writer.summary()
    if summary is not None:
        result['timeline'] = summary
    return result


def war_room_trackers_timeline_sync(war_room_id, tracker, access, user_id):
    """Catch the timelines up with a tracker whose sync was turned on
    after the fact: `state` mode writes the current state of every stored
    row the caller can see, `changes` mode the past changes not in the
    timelines yet (`_timeline_backfill`). Returns the timeline summary."""
    if tracker.timeline_sync is None:
        raise BusinessProcessingError('This tracker does not write to the timelines')
    if _writes_changes(tracker):
        return _timeline_backfill(war_room_id, tracker, access, user_id)
    cache = _SourceCache(war_room_id, access)
    if tracker.source == 'manual':
        pairs = _manual_pairs(war_room_trackers_db_rows(tracker.id), cache)
    else:
        pairs, _ = _mirror_pairs(tracker, cache)
        pairs = [(stored, item) for stored, item in pairs if stored is not None]
    step_names = {step.id: step.name for step in war_room_trackers_db_steps(war_room_id)}
    steps = {}
    for step in war_room_trackers_db_row_steps([stored.id for stored, _ in pairs]):
        steps.setdefault(step.row_id, set()).add(step.step_id)
    writer = WarRoomTrackerTimelineWriter(war_room_id, tracker, access.writable_case_ids, user_id, _utcnow(),
                                          step_names)
    writer.prefetch([stored.id for stored, _ in pairs])
    for stored, item in pairs:
        writer.row_state(stored, item, steps.get(stored.id, set()))
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('The timelines were changed meanwhile, retry')
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
    if len(events) >= WAR_ROOM_TRACKERS_TIMELINE_BACKFILL_LIMIT and len(groups) > 1:
        groups.pop()
    return groups


def war_room_trackers_values_before(values, later_value_events):
    """The values of a row before the `value_changed` events
    `later_value_events` (oldest first) were made."""
    out = dict(values or {})
    for event in reversed(later_value_events):
        details = event.details or {}
        key = details.get('column_key')
        if key is None:
            continue
        if details.get('old') is None:
            out.pop(key, None)
        else:
            out[key] = details['old']
    return out


def _timeline_backfill(war_room_id, tracker, access, user_id):
    """Write the past changes of a `changes` sync, oldest first, at most
    `WAR_ROOM_TRACKERS_TIMELINE_BACKFILL_LIMIT` per call. Only the changes
    of the rows the caller sees are handled and marked `in_timeline`; the
    others stay pending for the members who see them (the summary's
    `remaining` and `remaining_restricted`). A handled change of a case the
    caller cannot write is marked without being written, as live changes
    are: nothing is written twice. Each change names its author."""
    cache = _SourceCache(war_room_id, access)
    by_row = _visible_stored_rows(tracker, cache)
    groups = _history_groups(war_room_trackers_db_events_not_in_timeline(
        tracker.id, list(by_row), WAR_ROOM_TRACKERS_TIMELINE_BACKFILL_LIMIT))
    row_ids = sorted({group[0].row_id for group in groups})
    later = {}
    if groups:
        for event in war_room_trackers_db_value_events_after(row_ids, groups[0][0].id):
            later.setdefault(event.row_id, []).append(event)
    writer = WarRoomTrackerTimelineWriter(war_room_id, tracker, access.writable_case_ids, user_id, _utcnow(), {})
    for group in groups:
        for event in group:
            event.in_timeline = True
        stored, item = by_row[group[0].row_id]
        last_id = group[-1].id
        values = war_room_trackers_values_before(
            stored.values, [event for event in later.get(stored.id, ()) if event.id > last_id])
        writer.row_history(stored, item, [(event.action, event.details or {}) for event in group],
                           group[0].created_at, values, group[0].user_id)
    if not war_room_trackers_db_commit():
        raise BusinessProcessingError('The timelines were changed meanwhile, retry')
    writer.finish()
    summary = writer.summary()
    summary['remaining'] = war_room_trackers_db_events_not_in_timeline_count(tracker.id, list(by_row))
    pending = war_room_trackers_db_events_not_in_timeline_counts([tracker.id]).get(tracker.id, 0)
    summary['remaining_restricted'] = max(pending - summary['remaining'], 0)
    return summary


def _manual_row(tracker, row_id):
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows of a mirrored tracker follow the war room')
    rows = war_room_trackers_db_rows_by_ids(tracker.id, [row_id])
    if not rows:
        raise ObjectNotFoundError()
    return rows[0]


def war_room_trackers_rows_delete(tracker, row_id):
    """Returns the `(item_type, label)` of the deleted row."""
    row = _manual_row(tracker, row_id)
    ref = (row.item_type, row.label)
    war_room_trackers_db_delete(row)
    war_room_trackers_db_commit()
    return ref


def war_room_trackers_rows_reorder(tracker, ids):
    if tracker.source != 'manual':
        raise BusinessProcessingError('Rows of a mirrored tracker follow the war room')
    war_room_trackers_reorder(war_room_trackers_db_rows(tracker.id), ids)
    war_room_trackers_db_commit()


# --------------------------------------------------------------- Stream --

_STREAM_LISTED_ROWS = 3

_STREAM_ITEM_NOUNS = {
    'asset': ('asset', 'assets'),
    'ioc': ('IOC', 'IOCs'),
    'customer': ('customer', 'customers'),
    'case': ('case', 'cases'),
    'managed_asset': ('registry asset', 'registry assets'),
}


def war_room_trackers_describe_rows(refs):
    """The rows of a war-room stream line from their `(item_type, label)`.

    The stream is read by every member of the room while the rows of an
    item are bounded by the cases / registry scope of each reader: items
    are only counted per type, never named. Free-text rows, visible to
    the whole room, are named (or counted past a few)."""
    labels = []
    counts = {}
    for item_type, label in refs:
        if item_type is None:
            labels.append(label or 'a row')
        else:
            counts[item_type] = counts.get(item_type, 0) + 1
    labels = list(dict.fromkeys(labels))
    parts = labels if len(labels) <= _STREAM_LISTED_ROWS else [f'{len(labels)} rows']
    for item_type, count in counts.items():
        singular, plural = _STREAM_ITEM_NOUNS.get(item_type, ('item', 'items'))
        parts.append(f'{count} {singular if count == 1 else plural}')
    return ', '.join(parts)


def war_room_trackers_describe_changes(changes):
    """One war-room stream line for the `(item_type, label, events)` of a
    state update, None when nothing changed."""
    added = {}
    removed = {}
    edited = {}
    for item_type, label, events in changes:
        ref = (item_type, label)
        for action, details in events:
            if action == 'step_added':
                added.setdefault(details.get('step_name') or 'a step', []).append(ref)
            elif action == 'step_removed':
                removed.setdefault(details.get('step_name') or 'a step', []).append(ref)
            elif action == 'note_changed':
                edited.setdefault('the note', []).append(ref)
            elif action == 'label_changed':
                edited.setdefault('the label', []).append(ref)
            elif action == 'value_changed':
                edited.setdefault(details.get('column_label') or 'a column', []).append(ref)
    parts = [f'set {step} on {war_room_trackers_describe_rows(refs)}' for step, refs in added.items()]
    parts += [f'cleared {step} from {war_room_trackers_describe_rows(refs)}' for step, refs in removed.items()]
    parts += [f'edited {what} of {war_room_trackers_describe_rows(refs)}' for what, refs in edited.items()]
    return '; '.join(parts) or None


# -------------------------------------------------------------- History --

def _serialize_events(events, manual):
    keys = war_room_trackers_db_rows_keys({event.row_id for event in events})
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


def _visible_row_ids(tracker, cache):
    """Stored rows the caller sees: every one of a manual tracker, those of
    the current visible items of a mirror."""
    if tracker.source == 'manual':
        return None
    pairs, _ = _mirror_pairs(tracker, cache)
    return {stored.id for stored, _ in pairs if stored is not None}


def war_room_trackers_history(war_room_id, tracker, access, row_id=None, item_type=None, item_id=None):
    """Events of one row (by `row_id` or item) or the latest of the
    tracker, newest first. Rows of items the caller cannot see (mirrors)
    have no history for it."""
    cache = _SourceCache(war_room_id, access)
    visible = _visible_row_ids(tracker, cache)
    if row_id is not None:
        row_ids = [row_id]
    elif item_type is not None and item_id is not None:
        if tracker.source == 'manual':
            stored = [row.id for row in war_room_trackers_db_rows(tracker.id)
                      if row.item_type == item_type and row.item_id == item_id]
        else:
            stored = []
            items, _ = cache.items(tracker.source)
            stored_by_item = {(row.item_type, row.item_id): row for row in war_room_trackers_db_rows(tracker.id)
                              if row.item_type is not None}
            for item in items:
                if item['item_type'] == item_type and item_id in item['member_ids']:
                    match = war_room_trackers_match_stored(item, stored_by_item)
                    stored = [match.id] if match is not None else []
                    break
        row_ids = stored
    else:
        row_ids = None
    if row_ids is not None and visible is not None:
        row_ids = [rid for rid in row_ids if rid in visible]
    events = war_room_trackers_db_events(tracker.id, row_ids=row_ids, limit=WAR_ROOM_TRACKERS_HISTORY_LIMIT
                                         if row_ids is not None or visible is None
                                         else WAR_ROOM_TRACKERS_HISTORY_LIMIT * 5)
    if row_ids is None and visible is not None:
        events = [event for event in events if event.row_id in visible][:WAR_ROOM_TRACKERS_HISTORY_LIMIT]
    return _serialize_events(events, tracker.source == 'manual')


# ---------------------------------------------------------------- Board --

def _board_case_ids(item, readable_case_ids):
    """The readable cases a row belongs to (none for free text or a row
    the caller cannot see)."""
    if item is None:
        return []
    out = []
    for case_id, _, _ in item['timeline']['case_links']:
        if case_id in readable_case_ids and case_id not in out:
            out.append(case_id)
    return out


def _row_value(stored, column_key):
    return (stored.values or {}).get(column_key) if stored is not None else None


def war_room_trackers_board_row_matches(stored, row_filter):
    """Whether a row passes a board widget `filter` (`{column_key,
    values}`, None = every row): its value is one of `values`, or one of
    its values is for a multiselect."""
    if row_filter is None:
        return True
    value = _row_value(stored, row_filter['column_key'])
    if isinstance(value, list):
        return any(entry in row_filter['values'] for entry in value)
    return value is not None and value in row_filter['values']


def _board_rows(pairs, row_steps, step_order, row_filter):
    """`[(stored, item, step ids in the room's step order)]` of the rows
    passing `row_filter`."""
    rows = []
    for stored, item in pairs:
        if not war_room_trackers_board_row_matches(stored, row_filter):
            continue
        step_ids = row_steps.get(stored.id, ()) if stored is not None else ()
        rows.append((stored, item, sorted((sid for sid in step_ids if sid in step_order), key=step_order.get)))
    return rows


def _board_row(stored, item, step_ids, readable_case_ids, manual):
    if stored is None:
        return {
            'row_id': None, 'key': _row_key(item['item_type'], item['item_id'], None, manual),
            'item_type': item['item_type'], 'item_id': item['item_id'], 'label': item['label'],
            'custom_label': None, 'restricted': False, 'step_ids': [], 'case_ids': _board_case_ids(item, readable_case_ids),
            'updated_at': None,
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
        'custom_label': _custom_label(stored),
        'restricted': stored.item_type is not None and item is None,
        'step_ids': step_ids,
        'case_ids': _board_case_ids(item, readable_case_ids),
        'updated_at': _isoformat(stored.updated_at),
    }


def _shown_step_ids(config_step_ids, tracker, room_steps, rows):
    """The steps a widget shows: the configured ones, or the steps the
    tracker offers (plus the ones its rows still carry)."""
    room_step_ids = [step.id for step in room_steps]
    if config_step_ids is not None:
        return [sid for sid in config_step_ids if sid in room_step_ids]
    offered = _offered_step_ids(tracker, room_steps)
    carried = {sid for _, _, step_ids in rows for sid in step_ids}
    return [sid for sid in room_step_ids if sid in offered or sid in carried]


def _board_progress(tracker, rows, config, room_steps):
    step_ids = _shown_step_ids(config.get('step_ids'), tracker, room_steps, rows)
    counts = dict.fromkeys(step_ids, 0)
    without_step = 0
    for _, _, row_step_ids in rows:
        if not row_step_ids:
            without_step += 1
        for sid in row_step_ids:
            if sid in counts:
                counts[sid] += 1
    return {
        'rows_total': len(rows),
        'rows_without_step': without_step,
        'steps': [{'step_id': sid, 'count': counts[sid]} for sid in step_ids],
    }


def _carrying(rows, step_ids):
    """The rows carrying one of `step_ids`, every row when empty."""
    wanted = set(step_ids or ())
    if not wanted:
        return rows
    return [row for row in rows if wanted.intersection(row[2])]


def _board_count(rows, config):
    return {'count': len(_carrying(rows, config.get('step_ids'))), 'total': len(rows)}


def _board_list(rows, config, readable_case_ids, manual):
    listed = _carrying(rows, config.get('step_ids'))
    return {
        'rows': [_board_row(stored, item, step_ids, readable_case_ids, manual)
                 for stored, item, step_ids in listed[:WAR_ROOM_BOARD_LIST_ROWS]],
        'total': len(listed),
    }


def _breakdown_keys(column, value):
    if column['type'] == 'checkbox':
        return [value is True]
    if column['type'] == 'multiselect':
        keys = [entry for entry in value if entry in column['options']] if isinstance(value, list) else []
        return keys or [None]
    return [value] if value in column['options'] else [None]


def _board_breakdown(tracker, rows, config, room_steps):
    column = next((c for c in tracker.columns or () if c['key'] == config.get('column_key')), None)
    if column is None or column['type'] not in ('select', 'multiselect', 'checkbox'):
        return {'missing': 'column'}
    step_ids = _shown_step_ids(config.get('step_ids'), tracker, room_steps, rows)
    values = [True, False] if column['type'] == 'checkbox' else [*column['options'], None]
    groups = {value: {'value': value, 'rows': 0, 'counts': [0] * len(step_ids)} for value in values}
    for stored, _, row_step_ids in rows:
        for key in _breakdown_keys(column, _row_value(stored, column['key'])):
            group = groups[key]
            group['rows'] += 1
            for index, sid in enumerate(step_ids):
                if sid in row_step_ids:
                    group['counts'][index] += 1
    return {
        'column_label': column['label'],
        'column_type': column['type'],
        'step_ids': step_ids,
        'rows_total': len(rows),
        'groups': [group for value, group in groups.items() if value is not None or group['rows']],
    }


def _board_case_highlights(rows_by_tracker, highlight_step_ids, step_order, readable_case_ids):
    """`{case_id: [{step_id, count}]}`: per readable case, how many of its
    rows carry each highlighted step, in the room's step order."""
    highlight = set(highlight_step_ids)
    per_case = {}
    for rows in rows_by_tracker:
        for _, item, step_ids in rows:
            hits = [sid for sid in step_ids if sid in highlight]
            if not hits:
                continue
            for case_id in _board_case_ids(item, readable_case_ids):
                counts = per_case.setdefault(case_id, {})
                for sid in hits:
                    counts[sid] = counts.get(sid, 0) + 1
    return {case_id: [{'step_id': sid, 'count': count}
                      for sid, count in sorted(counts.items(), key=lambda entry: step_order[entry[0]])]
            for case_id, counts in per_case.items()}


def _board_widget_data(widget, tracker, pairs, truncated, ctx):
    room_steps, row_steps, step_order, readable = ctx
    config = widget['config']
    rows = _board_rows(pairs, row_steps, step_order, config.get('filter'))
    if widget['type'] == 'tracker_progress':
        data = _board_progress(tracker, rows, config, room_steps)
    elif widget['type'] == 'tracker_count':
        data = _board_count(rows, config)
    elif widget['type'] == 'tracker_list':
        data = _board_list(rows, config, readable, tracker.source == 'manual')
    else:
        data = _board_breakdown(tracker, rows, config, room_steps)
    data['truncated'] = truncated
    return data


def war_room_trackers_board_data(war_room_id, access, widgets):
    """What the board shows of the trackers, as the caller sees them (same
    rows as in the trackers tab): `{data, steps, case_highlights}`, with
    `data` the data of each tracker widget by widget id (`{missing:
    'tracker'}` when its tracker is gone), `steps` the room's steps and
    `case_highlights` the highlighted steps of the `cases` widget per
    readable case (see `_board_case_highlights`)."""
    tracker_widgets = [widget for widget in widgets if widget['type'] in TRACKER_WIDGET_TYPES]
    cases = next((widget for widget in widgets if widget['type'] == 'cases'), None)
    highlight = (cases or {}).get('config', {}).get('highlight_step_ids') or []
    room_steps = war_room_trackers_db_steps(war_room_id)
    result = {'data': {}, 'steps': [war_room_trackers_serialize_step(step) for step in room_steps],
              'case_highlights': {}}
    trackers = {tracker.id: tracker for tracker in war_room_trackers_db_trackers(war_room_id)}
    needed = {widget['config'].get('tracker_id') for widget in tracker_widgets} & trackers.keys()
    highlighted = []
    if highlight:
        tracker_ids = cases['config'].get('tracker_ids')
        highlighted = [tracker_id for tracker_id in trackers if tracker_ids is None or tracker_id in tracker_ids]
        needed.update(highlighted)
    cache = _SourceCache(war_room_id, access)
    listed = {tracker_id: _tracker_pairs(trackers[tracker_id], cache) for tracker_id in sorted(needed)}
    row_steps = {}
    stored_ids = [stored.id for pairs, _ in listed.values() for stored, _ in pairs if stored is not None]
    for step in war_room_trackers_db_row_steps(stored_ids):
        row_steps.setdefault(step.row_id, set()).add(step.step_id)
    step_order = {step.id: index for index, step in enumerate(room_steps)}
    readable = set(access.readable_case_ids)
    ctx = (room_steps, row_steps, step_order, readable)
    for widget in tracker_widgets:
        tracker = trackers.get(widget['config'].get('tracker_id'))
        if tracker is None:
            result['data'][widget['id']] = {'missing': 'tracker'}
            continue
        data = _board_widget_data(widget, tracker, *listed[tracker.id], ctx)
        data['tracker'] = {'id': tracker.id, 'name': tracker.name, 'icon': tracker.icon, 'source': tracker.source}
        result['data'][widget['id']] = data
    result['case_highlights'] = _board_case_highlights(
        [_board_rows(listed[tracker_id][0], row_steps, step_order, None) for tracker_id in highlighted],
        highlight, step_order, readable)
    return result
