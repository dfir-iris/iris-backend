#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Core notification listeners.

Wired into the module-hook system as *built-in* handlers (not via the
`IrisModule` table). We hook the same events modules can register for,
but we bypass the module dispatcher — one direct call registered via
`register_notification_listeners()` when `app` is imported.

The design keeps each listener defensive:

* Any exception is swallowed and logged. A save flow (create note,
  update task, close case) MUST NOT fail because a notification
  couldn't be fired.
* Actor exclusion is done centrally — `_actor_id()` reads
  `iris_current_user` when available, else None (background/celery
  contexts).
"""

from __future__ import annotations

import logging
from typing import Any
from typing import Callable
from typing import Optional

from app.iris_engine.module_handler import module_handler as _mh
from app.iris_engine.notifications.mentions import extract_mentioned_user_ids
from app.iris_engine.notifications.mentions import mentions_added_user_ids
from app.iris_engine.notifications.service import notify
from app.iris_engine.notifications.service import notify_many


logger = logging.getLogger(__name__)


def _actor_id() -> Optional[int]:
    """Return the current authenticated user's id, or None outside a
    request. `iris_current_user` is a LocalProxy so we can't just do
    `getattr(..., 'id', None)` — accessing `id` when there is no user
    triggers the proxy which may raise."""
    try:
        from app.blueprints.iris_user import iris_current_user
        # `iris_current_user.id` throws when there is no auth context
        # (Celery worker, CLI); guard with a getattr on the proxied
        # object.
        user = iris_current_user._get_current_object()  # type: ignore[attr-defined]
        if user is None:
            return None
        return int(getattr(user, 'id', 0)) or None
    except Exception:
        return None


def _safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a listener so exceptions never bubble into the caller."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception('Notification listener %s failed', fn.__name__)
            return None
    wrapped.__name__ = fn.__name__
    return wrapped


# --- Note listeners ---------------------------------------------------------

@_safe
def _on_note_create(note) -> None:
    """Fire mention notifications for a new note.

    Uses the extractor which handles both TipTap mention spans and
    legacy `@handle` text.
    """
    if note is None:
        return
    _notify_note_mentions(note, extract_mentioned_user_ids(getattr(note, 'note_content', None)))


@_safe
def notifications_note_updated(note, previous_content: Optional[str]) -> None:
    """Fire mention notifications for the mentions a note save added.

    Called by `notes_update` with the content from before the save, not
    hooked on `on_postload_note_update`: the hook only carries the saved
    note, and notifying every mention it holds re-pinged everybody on
    each save.
    """
    if note is None:
        return
    _notify_note_mentions(note, mentions_added_user_ids(previous_content,
                                                        getattr(note, 'note_content', None)))


def _notify_note_mentions(note, user_ids) -> None:
    if not user_ids:
        return

    title = 'You were mentioned in a note'
    body = getattr(note, 'note_title', None) or ''
    case_id = getattr(note, 'note_case_id', None)
    note_id = getattr(note, 'note_id', None)
    link = f'/case/{case_id}/notes/{note_id}' if case_id and note_id else None

    notify_many(
        user_ids=user_ids,
        event_type='mention',
        title=title,
        body=body,
        link=link,
        source_type='note',
        source_id=note_id,
        exclude_user_ids=[_actor_id()] if _actor_id() else [],
    )


# --- Comment listeners ------------------------------------------------------

_COMMENT_HOOK_LINKS = {
    # Maps hook name to a builder returning (link, parent_type, parent_id)
    # given the hook payload dict. Each comment kind has its own payload
    # shape so this is table-driven rather than a big if/else.
    'note': lambda p: (
        f'/case/{p["note"].note_case_id}/notes/{p["note"].note_id}',
        'note', p['note'].note_id, p['note'].note_case_id,
    ),
    'task': lambda p: (
        f'/case/{p["task"].task_case_id}/tasks/{p["task"].id}',
        'task', p['task'].id, p['task'].task_case_id,
    ),
    'asset': lambda p: (
        f'/case/{p["asset"].case_id}/assets/{p["asset"].asset_id}',
        'asset', p['asset'].asset_id, p['asset'].case_id,
    ),
    'evidence': lambda p: (
        f'/case/{p["evidence"].case_id}/evidence/{p["evidence"].id}',
        'evidence', p['evidence'].id, p['evidence'].case_id,
    ),
    'ioc': lambda p: (
        f'/case/{p["ioc"].case_id}/iocs/{p["ioc"].ioc_id}',
        'ioc', p['ioc'].ioc_id, p['ioc'].case_id,
    ),
    'event': lambda p: (
        f'/case/{p["event"].case_id}/timeline/{p["event"].event_id}',
        'event', p['event'].event_id, p['event'].case_id,
    ),
    'alert': lambda p: (
        f'/alerts/{p["alert"].alert_id}',
        'alert', p['alert'].alert_id, None,
    ),
}


def _make_comment_listener(kind: str):
    @_safe
    def _listener(payload) -> None:
        if not isinstance(payload, dict) or 'comment' not in payload:
            return
        comment = payload['comment']
        content = getattr(comment, 'comment_text', None)
        user_ids = extract_mentioned_user_ids(content)
        if not user_ids:
            return
        builder = _COMMENT_HOOK_LINKS.get(kind)
        if builder is None:
            return
        link, source_type, _, _case_id = builder(payload)

        notify_many(
            user_ids=user_ids,
            event_type='mention',
            title=f'You were mentioned in a {kind} comment',
            body=(content or '')[:255],
            link=link,
            source_type=f'{source_type}_comment',
            source_id=getattr(comment, 'comment_id', None),
            exclude_user_ids=[_actor_id()] if _actor_id() else [],
        )
    _listener.__name__ = f'_on_{kind}_comment'
    return _listener


# --- Task listeners ---------------------------------------------------------

@_safe
def _on_task_create(task) -> None:
    """Notify assignees on case-task create.

    Case tasks use the `task_assignee` join table for their assignees
    (see models.TaskAssignee); we read the current set at fire time.
    """
    if task is None:
        return
    task_id = getattr(task, 'id', None)
    if not task_id:
        return
    # Import here to keep this module import-cheap (models pull in the
    # whole SQLAlchemy tree).
    from app.models.models import TaskAssignee
    assignee_ids = [
        row.user_id for row in
        TaskAssignee.query.filter(TaskAssignee.task_id == task_id).all()
    ]
    _notify_task_assignees(task, assignee_ids)


@_safe
def notifications_task_assignees_added(task, user_ids) -> None:
    """Notify the assignees a case-task update added.

    Called by `tasks_update` rather than hooked on
    `on_postload_task_update`, which only carries the saved task: telling
    every assignee re-notified them on each edit.
    """
    if task is None:
        return
    _notify_task_assignees(task, user_ids)


def _notify_task_assignees(task, assignee_ids) -> None:
    task_id = getattr(task, 'id', None)
    if not task_id or not assignee_ids:
        return
    title = getattr(task, 'task_title', 'A task')
    case_id = getattr(task, 'task_case_id', None)
    link = f'/case/{case_id}/tasks/{task_id}' if case_id else None
    notify_many(
        user_ids=assignee_ids,
        event_type='task_assigned',
        title='You were assigned a task',
        body=title,
        link=link,
        source_type='task',
        source_id=task_id,
        exclude_user_ids=[_actor_id()] if _actor_id() else [],
    )


@_safe
def _on_global_task(task) -> None:
    """Notify the assignee on global-task create/update. Uses the
    scalar `task_assignee_id` on GlobalTasks (no join table)."""
    if task is None:
        return
    assignee = getattr(task, 'task_assignee_id', None)
    if not assignee:
        return
    task_id = getattr(task, 'id', None)
    title = getattr(task, 'task_title', 'A task')
    notify(
        user_id=int(assignee),
        event_type='task_assigned',
        title='You were assigned a global task',
        body=title,
        link=f'/dim-tasks/{task_id}' if task_id else None,
        source_type='global_task',
        source_id=task_id,
    ) if _actor_id() != int(assignee) else None


# --- Case listeners ---------------------------------------------------------

@_safe
def _on_case_create(case) -> None:
    """New case with an owner other than the actor → notify."""
    if case is None:
        return
    owner_id = getattr(case, 'owner_id', None)
    if not owner_id:
        return
    if _actor_id() == int(owner_id):
        return
    case_id = getattr(case, 'case_id', None)
    notify(
        user_id=int(owner_id),
        event_type='case_assigned',
        title='You own a new case',
        body=getattr(case, 'name', None) or '',
        link=f'/case/{case_id}' if case_id else None,
        source_type='case',
        source_id=case_id,
    )


@_safe
def notifications_case_updated(case, previous_state_id: Optional[int],
                               previous_owner_id: Optional[int],
                               previous_reviewer_id: Optional[int]) -> None:
    """Notify what a case update changed: a new reviewer, a new owner, or
    the owner when the state moved.

    Called by the case update / close / reopen paths with the values from
    before the change, not hooked on `on_postload_case_update`: the hook
    only carries the saved case, and notifying from it pinged the owner
    and the reviewer on every save — editing the summary included."""
    if case is None:
        return
    case_id = getattr(case, 'case_id', None)
    if not case_id:
        return
    link = f'/case/{case_id}'
    body = getattr(case, 'name', None) or ''
    actor_id = _actor_id()

    reviewer_id = getattr(case, 'reviewer_id', None)
    if reviewer_id and reviewer_id != previous_reviewer_id and actor_id != int(reviewer_id):
        notify(
            user_id=int(reviewer_id),
            event_type='case_assigned',
            title='You were assigned as a case reviewer',
            body=body,
            link=link,
            source_type='case',
            source_id=case_id,
        )

    owner_id = getattr(case, 'owner_id', None)
    if not owner_id or actor_id == int(owner_id):
        return
    if owner_id != previous_owner_id:
        notify(
            user_id=int(owner_id),
            event_type='case_assigned',
            title='You were made the owner of a case',
            body=body,
            link=link,
            source_type='case',
            source_id=case_id,
        )
    elif getattr(case, 'state_id', None) != previous_state_id:
        notify(
            user_id=int(owner_id),
            event_type='case_state_change',
            title='The state of a case you own changed',
            body=body,
            link=link,
            source_type='case',
            source_id=case_id,
        )


# --- Alert listeners --------------------------------------------------------

@_safe
def _on_alert(alert) -> None:
    if alert is None:
        return
    owner_id = getattr(alert, 'alert_owner_id', None)
    if not owner_id or _actor_id() == int(owner_id):
        return
    alert_id = getattr(alert, 'alert_id', None)
    notify(
        user_id=int(owner_id),
        event_type='alert_assigned',
        title='An alert was assigned to you',
        body=getattr(alert, 'alert_title', None) or '',
        link=f'/alerts/{alert_id}' if alert_id else None,
        source_type='alert',
        source_id=alert_id,
    )


@_safe
def _on_alert_escalate(alert) -> None:
    if alert is None:
        return
    owner_id = getattr(alert, 'alert_owner_id', None)
    if not owner_id or _actor_id() == int(owner_id):
        return
    alert_id = getattr(alert, 'alert_id', None)
    notify(
        user_id=int(owner_id),
        event_type='alert_escalated',
        title='An alert you own was escalated',
        body=getattr(alert, 'alert_title', None) or '',
        link=f'/alerts/{alert_id}' if alert_id else None,
        source_type='alert',
        source_id=alert_id,
    )


# --- Registration -----------------------------------------------------------

# Map hook name -> listener. Populated at import time so a single
# `register_notification_listeners()` call wires everything.
_HOOK_MAP = {
    # Notes — updates go through `notifications_note_updated`
    'on_postload_note_create': _on_note_create,
    # Comments (one listener per parent kind because payload shape
    # differs slightly per kind — see _make_comment_listener).
    'on_postload_note_commented': _make_comment_listener('note'),
    'on_postload_note_comment_update': _make_comment_listener('note'),
    'on_postload_task_commented': _make_comment_listener('task'),
    'on_postload_task_comment_update': _make_comment_listener('task'),
    'on_postload_asset_commented': _make_comment_listener('asset'),
    'on_postload_asset_comment_update': _make_comment_listener('asset'),
    'on_postload_evidence_commented': _make_comment_listener('evidence'),
    'on_postload_evidence_comment_update': _make_comment_listener('evidence'),
    'on_postload_ioc_commented': _make_comment_listener('ioc'),
    'on_postload_ioc_comment_update': _make_comment_listener('ioc'),
    'on_postload_event_commented': _make_comment_listener('event'),
    'on_postload_event_comment_update': _make_comment_listener('event'),
    'on_postload_alert_commented': _make_comment_listener('alert'),
    'on_postload_alert_comment_update': _make_comment_listener('alert'),
    # Tasks — updates go through `notifications_task_assignees_added`
    'on_postload_task_create': _on_task_create,
    'on_postload_global_task_create': _on_global_task,
    'on_postload_global_task_update': _on_global_task,
    # Cases — updates go through `notifications_case_updated`
    'on_postload_case_create': _on_case_create,
    # Alerts
    'on_postload_alert_create': _on_alert,
    'on_postload_alert_update': _on_alert,
    'on_postload_alert_escalate': _on_alert_escalate,
}


def _on_hook(hook_name: str, data: Any, caseid: Optional[int] = None,
             hook_ui_name: Optional[str] = None) -> None:
    listener = _HOOK_MAP.get(hook_name)
    if listener is not None and data is not None:
        listener(data)


def register_notification_listeners() -> None:
    """Attach the listeners to `call_modules_hook`.

    The dispatcher is the single choke point for every hook fire in the
    app; it calls built-in listeners after the modules, with the data the
    modules returned, so notifications reflect the final state.
    Registering twice is a no-op.
    """
    _mh.register_builtin_hook_listener(_on_hook)
