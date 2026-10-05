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

"""Synthetic hook data for previews and test sends.

Used when no real delivery of the event exists yet. Built from the
model's columns so field names match what the real event carries,
without reading any actual case data.
"""

import datetime
import uuid

from app.db import db


_MODELS = {
    'case': 'Cases',
    'alert': 'Alert',
    'alert_cluster': 'AlertCluster',
    'asset': 'CaseAssets',
    'ioc': 'Ioc',
    'note': 'Notes',
    'event': 'CasesEvent',
    'evidence': 'CaseReceivedFile',
    'task': 'CaseTasks',
    'global_task': 'GlobalTasks',
    'notification': 'Notification',
    'war_room': 'WarRoom',
    'comment': 'Comments',
}

_SENSITIVE_MARKERS = ('password', 'api_key', 'secret', 'token', 'mfa')


def _model_class(name):
    for mapper in db.Model.registry.mappers:
        if mapper.class_.__name__ == name:
            return mapper.class_
    return None


def _placeholder(column, label):
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return None
    if column.primary_key or column.name.endswith('_id'):
        return 1
    if python_type is bool:
        return False
    if python_type is int:
        return 1
    if python_type is float:
        return 1.0
    if python_type is datetime.datetime:
        return datetime.datetime(2026, 1, 1, 12, 0, 0).isoformat()
    if python_type is datetime.date:
        return '2026-01-01'
    if python_type is uuid.UUID:
        return '00000000-0000-4000-8000-000000000001'
    if python_type in (dict, list):
        return python_type()
    if python_type is str:
        return f'Sample {label} {column.name.replace("_", " ")}'
    return None


def _sample_object(object_type):
    model = _model_class(_MODELS.get(object_type, ''))
    label = object_type.replace('_', ' ')
    if model is None:
        return {'id': 1, 'name': f'Sample {label}'}
    sample = {}
    for column in model.__table__.columns:
        if any(marker in column.name.lower() for marker in _SENSITIVE_MARKERS):
            continue
        sample[column.name] = _placeholder(column, label)
    if object_type == 'notification':
        sample['link'] = '/case/1'
    return sample


def webhooks_sample_data(object_type, action):
    """Data shaped like what the hook passes for this object and action."""
    if action.endswith('delete'):
        return 1
    if action in ('commented', 'comment_update'):
        return {'comment': _sample_object('comment'),
                object_type: _sample_object(object_type)}
    if object_type == 'alert_cluster' and action in ('alert_add', 'alert_remove', 'merge', 'escalate'):
        return {'cluster_id': 1, 'alert_ids': [1, 2]}
    return _sample_object(object_type)
