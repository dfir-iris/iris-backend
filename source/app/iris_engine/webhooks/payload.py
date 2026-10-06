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

"""Turn a hook fire into a webhook event.

Hooks pass whatever the caller had at hand: a model instance, a list of
them, a dict, or a bare id for deletions. `webhooks_serialize` makes any
of those JSON-safe (models go through their API schema when one is
known, else a column dump that drops credential-looking fields), and
`webhooks_make_event` wraps the result in the IRIS envelope with a
human title / summary and a link back to the UI.

The event is built in the request that fired the hook — the objects are
still attached to its session there — and travels to Celery as JSON.
"""

import datetime
import decimal
import enum
import json
import uuid

from sqlalchemy import inspect as sa_inspect

from app.iris_engine.webhooks.events import MANUAL_ACTION
from app.iris_engine.webhooks.events import webhooks_action_label
from app.iris_engine.webhooks.events import webhooks_event_label
from app.iris_engine.webhooks.events import webhooks_object_label
from app.iris_engine.webhooks.events import webhooks_split_event


# Model class name -> (schema class name, schema kwargs). Resolved lazily:
# marshables imports half the app.
_SCHEMAS = {
    'Cases': ('CaseSchemaForAPIV2', {}),
    'Alert': ('AlertSchema', {}),
    'CaseAssets': ('CaseAssetsSchema', {'exclude': ['alerts']}),
    'Ioc': ('IocSchemaForAPIV2', {}),
    'Notes': ('CaseNoteSchema', {}),
    'CasesEvent': ('EventSchema', {}),
    'CaseReceivedFile': ('CaseEvidenceSchema', {}),
    'CaseTasks': ('CaseTaskSchema', {}),
    'GlobalTasks': ('GlobalTasksSchema', {}),
    'Comments': ('CommentSchema', {}),
    'AlertCluster': ('AlertClusterSchema', {}),
}

_SENSITIVE_MARKERS = ('password', 'api_key', 'secret', 'token', 'mfa')

_MAX_DEPTH = 8

_NAME_KEYS = (
    'case_name', 'alert_title', 'cluster_title', 'note_title', 'task_title', 'event_title',
    'asset_name', 'ioc_value', 'file_original_name', 'filename', 'identifier', 'title', 'name',
)

_ID_KEYS = {
    'alert_cluster': ('cluster_id', 'id'),
    'war_room': ('war_room_id', 'id'),
    'vulnerability': ('vulnerability_id', 'id'),
    'vulnerability_finding': ('finding_id', 'id'),
    'case': ('case_id', 'id'),
}

_CASE_ID_KEYS = ('case_id', 'task_case_id', 'note_case_id', 'event_case_id', 'ioc_case_id')


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (bytes, bytearray)):
        return None
    return str(value)


def _is_model(value) -> bool:
    return hasattr(value, '__table__') and hasattr(value, '__mapper__')


def _dump_columns(obj) -> dict:
    result = {}
    for attr in sa_inspect(obj).mapper.column_attrs:
        key = attr.key
        if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
            continue
        result[key] = getattr(obj, key, None)
    return result


def _dump_model(obj):
    entry = _SCHEMAS.get(type(obj).__name__)
    if entry is not None:
        from app.schema import marshables
        schema_name, kwargs = entry
        try:
            return getattr(marshables, schema_name)(**kwargs).dump(obj)
        except Exception:
            # A schema that can't dump this instance (detached relation,
            # transient object) must not lose the event — fall through.
            pass
    return _dump_columns(obj)


def _to_plain(value, depth=0):
    if depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _is_model(value):
        return _to_plain(_dump_model(value), depth + 1)
    if isinstance(value, dict):
        return {str(k): _to_plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(v, depth + 1) for v in value]
    return value


def webhooks_serialize(data):
    """JSON-safe copy of hook data (round-tripped through `json`)."""
    return json.loads(json.dumps(_to_plain(data), default=_json_default))


def _subject(data, object_type):
    """The dict describing the object the event is about.

    Comment events carry `{'comment': ..., '<parent>': ...}`, list hooks
    carry a list, delete hooks often carry a bare id.
    """
    if isinstance(data, dict):
        nested = data.get(object_type)
        if isinstance(nested, dict):
            return nested
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def _object_id(data, subject, object_type):
    if isinstance(data, (int, str)) and not isinstance(data, bool):
        return data
    for key in _ID_KEYS.get(object_type, (f'{object_type}_id', 'id')):
        if subject.get(key) not in (None, ''):
            return subject.get(key)
    return None


def _object_name(subject, object_id):
    for key in _NAME_KEYS:
        value = subject.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if object_id is not None:
        return f'#{object_id}'
    return ''


def webhooks_case_id(data, object_type, caseid=None):
    if caseid:
        return caseid
    subject = _subject(data, object_type)
    if object_type == 'case':
        object_id = _object_id(data, subject, 'case')
        if object_id:
            return object_id
    for key in _CASE_ID_KEYS:
        if subject.get(key):
            return subject.get(key)
    nested = subject.get('case')
    if isinstance(nested, dict):
        return nested.get('case_id') or nested.get('id')
    return None


def _ui_path(object_type, action, subject, object_id, case_id):
    if action.endswith('delete'):
        return f'/case/{case_id}' if case_id and object_type != 'case' else None
    if object_type == 'notification':
        link = subject.get('link')
        return link if isinstance(link, str) and link.startswith('/') else None
    if object_type == 'alert' and object_id:
        return f'/alerts/{object_id}'
    if object_type == 'alert_cluster' and object_id:
        return f'/alert-clusters/{object_id}'
    if object_type == 'vulnerability':
        vulnerability_id = subject.get('vulnerability_id')
        return f'/manage/vulnerabilities/{vulnerability_id}' if vulnerability_id else None
    if object_type == 'war_room':
        war_room_id = subject.get('war_room_id') or (object_id if action in ('create', 'update', 'archive',
                                                                            'unarchive') else None)
        return f'/war-rooms/{war_room_id}' if war_room_id else None
    if not case_id:
        return None
    case_paths = {
        'asset': 'assets',
        'ioc': 'iocs',
        'note': 'notes',
        'task': 'tasks',
        'evidence': 'evidence',
    }
    if object_type in case_paths and object_id and not action.startswith('comment'):
        return f'/case/{case_id}/{case_paths[object_type]}/{object_id}'
    if object_type == 'event':
        return f'/case/{case_id}/timeline'
    return f'/case/{case_id}'


def webhooks_make_event(hook_name, data, case=None, actor=None, instance_url='', version='', timestamp=None):
    """The IRIS envelope for `data` (already serialized).

    `case` is `{id, name}` or None, `actor` is `{id, login, name}` or None
    (hooks fired from a Celery task have no user). Per-delivery fields
    (`delivery_id`, `webhook`) are added at send time.
    """
    object_type, action = webhooks_split_event(hook_name)
    event_label = webhooks_event_label(hook_name)
    subject = _subject(data, object_type)
    object_id = _object_id(data, subject, object_type)
    object_name = _object_name(subject, object_id)
    case_id = case.get('id') if case else None

    path = _ui_path(object_type, action, subject, object_id, case_id)
    base = (instance_url or '').rstrip('/')
    url = f'{base}{path}' if path and base else None

    if object_type == 'notification':
        title = subject.get('title') or event_label
        summary = subject.get('body') or title
    else:
        title = f'{event_label}: {object_name}' if object_name else event_label
        who = (actor.get('name') or actor.get('login')) if actor else 'IRIS'
        if action == MANUAL_ACTION:
            what = 'sent'
        else:
            what = webhooks_action_label(action) if action else 'triggered'
        summary = f'{who} {what} {webhooks_object_label(object_type).lower()}'
        if object_name:
            summary = f'{summary} {object_name}'
        if action in ('commented', 'comment_update') and isinstance(data, dict):
            comment = data.get('comment')
            text = comment.get('comment_text') if isinstance(comment, dict) else None
            if text:
                summary = f'{summary}: {text[:280]}'
        if case and object_type != 'case':
            summary = f'{summary} in case #{case_id} {case.get("name") or ""}'.rstrip()

    stamp = timestamp or datetime.datetime.now(datetime.timezone.utc)
    return {
        'event': hook_name,
        'event_label': event_label,
        'object_type': object_type,
        'action': action,
        'object_id': object_id,
        'timestamp': stamp.isoformat(),
        'title': title,
        'summary': summary,
        'url': url,
        'case': case,
        'actor': actor,
        'data': data,
        'iris': {'url': base or None, 'version': version},
    }
