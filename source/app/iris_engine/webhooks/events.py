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

"""Webhook event vocabulary.

A webhook event is an `on_postload_*` hook, or an `on_manual_trigger_*`
hook: a user picking the webhook in an object's menu. Its name is split
into the object it is about and the action
(`on_postload_alert_cluster_merge` → `alert_cluster` / `merge`,
`on_manual_trigger_ioc` → `ioc` / `manual_trigger`), which gives the
envelope its `object_type` / `action` fields and the UI its human
labels. Pure functions, no DB.
"""

EVENT_PREFIX = 'on_postload_'
MANUAL_PREFIX = 'on_manual_trigger_'
MANUAL_ACTION = 'manual_trigger'

# Longest first: `alert_cluster_create` must not parse as `alert`
_OBJECT_TYPES = (
    'activities_report',
    'alert_cluster',
    'global_task',
    'notification',
    'war_room',
    'evidence',
    'report',
    'asset',
    'alert',
    'event',
    'case',
    'note',
    'task',
    'ioc',
)

_OBJECT_LABELS = {
    'activities_report': 'Activities report',
    'alert_cluster': 'Alert cluster',
    'global_task': 'Global task',
    'notification': 'Notification',
    'war_room': 'War room',
    'evidence': 'Evidence',
    'report': 'Report',
    'asset': 'Asset',
    'alert': 'Alert',
    'event': 'Timeline event',
    'case': 'Case',
    'note': 'Note',
    'task': 'Task',
    'ioc': 'IOC',
}

_SPECIAL_ACTIONS = {
    'commented': 'comment added',
    'comment_update': 'comment updated',
    'comment_delete': 'comment deleted',
    'alert_add': 'alerts added',
    'alert_remove': 'alert removed',
    'status_update': 'status updated',
    'resolution_update': 'resolution updated',
    'case_attach': 'case attached',
    'case_detach': 'case detached',
    'member_add': 'member added',
    'member_remove': 'member removed',
    'poll_vote': 'poll vote',
    'reaction_toggle': 'reaction toggled',
    'message_pin': 'message pinned',
    MANUAL_ACTION: 'manual trigger',
}

_PAST_TENSE = {
    'create': 'created',
    'update': 'updated',
    'delete': 'deleted',
    'escalate': 'escalated',
    'merge': 'merged',
    'unmerge': 'unmerged',
    'archive': 'archived',
    'unarchive': 'unarchived',
    'publish': 'published',
    'close': 'closed',
    'reopen': 'reopened',
    'add': 'added',
    'remove': 'removed',
    'attach': 'attached',
    'detach': 'detached',
    'pin': 'pinned',
    'toggle': 'toggled',
}


def webhooks_is_manual(hook_name) -> bool:
    return isinstance(hook_name, str) and hook_name.startswith(MANUAL_PREFIX)


def webhooks_is_event(hook_name) -> bool:
    """Whether a webhook can subscribe to `hook_name` (postload or manual)."""
    return isinstance(hook_name, str) and (hook_name.startswith(EVENT_PREFIX) or webhooks_is_manual(hook_name))


def webhooks_split_event(hook_name: str) -> tuple:
    """(object_type, action) for a postload or manual hook name."""
    if webhooks_is_manual(hook_name):
        return hook_name[len(MANUAL_PREFIX):], MANUAL_ACTION
    rest = hook_name[len(EVENT_PREFIX):] if webhooks_is_event(hook_name) else hook_name
    for object_type in _OBJECT_TYPES:
        if rest == object_type:
            return object_type, ''
        if rest.startswith(f'{object_type}_'):
            return object_type, rest[len(object_type) + 1:]
    head, _, tail = rest.partition('_')
    return head, tail


def webhooks_object_label(object_type: str) -> str:
    return _OBJECT_LABELS.get(object_type, object_type.replace('_', ' ').capitalize())


def webhooks_action_label(action: str) -> str:
    """'create' → 'created', 'note_create' → 'note created'."""
    if action in _SPECIAL_ACTIONS:
        return _SPECIAL_ACTIONS[action]
    words = action.split('_')
    if not words or not words[-1]:
        return action
    words[-1] = _PAST_TENSE.get(words[-1], words[-1])
    return ' '.join(words)


def webhooks_event_label(hook_name: str) -> str:
    """'on_postload_war_room_note_create' → 'War room note created'."""
    object_type, action = webhooks_split_event(hook_name)
    label = webhooks_object_label(object_type)
    if not action:
        return label
    return f'{label} {webhooks_action_label(action)}'


def webhooks_event_matches(subscribed, hook_name: str) -> bool:
    """Whether a webhook subscribed to `subscribed` fires on `hook_name`.

    The wildcard covers postload events only: a manual trigger puts the
    webhook in an object's menu, which has to be asked for one by one.
    """
    if not webhooks_is_event(hook_name) or not subscribed:
        return False
    if hook_name in subscribed:
        return True
    return '*' in subscribed and not webhooks_is_manual(hook_name)


def webhooks_build_catalogue(hooks) -> list:
    """Catalogue entries for the UI, from (hook_name, description) pairs.

    Postload and manual hooks only — a preload hook fires before the
    change is committed and may still be rejected, which is not
    something an external system should act on.
    """
    catalogue = []
    for hook_name, description in hooks:
        if not webhooks_is_event(hook_name):
            continue
        object_type, action = webhooks_split_event(hook_name)
        catalogue.append({
            'name': hook_name,
            'object_type': object_type,
            'object_label': webhooks_object_label(object_type),
            'action': action,
            'label': webhooks_event_label(hook_name),
            'description': description or '',
            'manual': webhooks_is_manual(hook_name),
        })
    catalogue.sort(key=lambda e: (e['object_label'], e['action']))
    return catalogue
