#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Unit tests for the notifications of a case, note and task update: only
what the update changed notifies, so saving again pings nobody."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.notifications.hook_listeners import _HOOK_MAP
from app.iris_engine.notifications.hook_listeners import notifications_case_updated
from app.iris_engine.notifications.hook_listeners import notifications_note_updated
from app.iris_engine.notifications.hook_listeners import notifications_task_assignees_added

_MODULE = 'app.iris_engine.notifications.hook_listeners'
_ACTOR = 1
_OWNER = 2
_REVIEWER = 3
_OPEN = 10
_CLOSED = 11


def _case(state_id=_OPEN, owner_id=_OWNER, reviewer_id=_REVIEWER):
    return SimpleNamespace(case_id=4, name='#4 - case', state_id=state_id, owner_id=owner_id,
                           reviewer_id=reviewer_id)


def _mention(user_id):
    return f'<span data-mention data-kind="user" data-id="{user_id}" data-label="u">@u</span>'


class _NotificationsTestCase(TestCase):

    def setUp(self):
        patcher = patch(f'{_MODULE}._actor_id', return_value=_ACTOR)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notify = self._patch('notify')
        self.notify_many = self._patch('notify_many')

    def _patch(self, name):
        patcher = patch(f'{_MODULE}.{name}')
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _notified(self):
        return [(c.kwargs['user_id'], c.kwargs['event_type']) for c in self.notify.call_args_list]


class TestCaseUpdated(_NotificationsTestCase):

    def test_should_notify_nobody_when_nothing_they_care_about_changed(self):
        notifications_case_updated(_case(), _OPEN, _OWNER, _REVIEWER)
        self.notify.assert_not_called()

    def test_should_notify_a_new_reviewer(self):
        notifications_case_updated(_case(), _OPEN, _OWNER, None)
        self.assertEqual([(_REVIEWER, 'case_assigned')], self._notified())

    def test_should_notify_a_new_owner(self):
        notifications_case_updated(_case(), _OPEN, 9, _REVIEWER)
        self.assertEqual([(_OWNER, 'case_assigned')], self._notified())

    def test_should_notify_the_owner_of_a_state_change(self):
        notifications_case_updated(_case(state_id=_CLOSED), _OPEN, _OWNER, _REVIEWER)
        self.assertEqual([(_OWNER, 'case_state_change')], self._notified())

    def test_should_not_notify_the_actor(self):
        notifications_case_updated(_case(state_id=_CLOSED, owner_id=_ACTOR, reviewer_id=_ACTOR), _OPEN, 9, None)
        self.notify.assert_not_called()


class TestNoteUpdated(_NotificationsTestCase):

    def _note(self, content):
        return SimpleNamespace(note_id=5, note_case_id=4, note_title='note', note_content=content)

    def test_should_notify_nobody_when_saving_unchanged_mentions(self):
        notifications_note_updated(self._note(f'{_mention(7)} edited'), _mention(7))
        self.notify_many.assert_not_called()

    def test_should_notify_only_the_added_mention(self):
        notifications_note_updated(self._note(f'{_mention(7)} {_mention(8)}'), _mention(7))
        self.assertEqual({8}, set(self.notify_many.call_args.kwargs['user_ids']))


class TestTaskAssigneesAdded(_NotificationsTestCase):

    def test_should_notify_the_added_assignees(self):
        notifications_task_assignees_added(SimpleNamespace(id=6, task_case_id=4, task_title='task'), {7})
        self.assertEqual({7}, set(self.notify_many.call_args.kwargs['user_ids']))

    def test_should_notify_nobody_without_added_assignee(self):
        notifications_task_assignees_added(SimpleNamespace(id=6, task_case_id=4, task_title='task'), set())
        self.notify_many.assert_not_called()


class TestUpdateHooks(TestCase):

    def test_should_not_notify_from_the_update_hooks(self):
        for hook in ('on_postload_note_update', 'on_postload_task_update', 'on_postload_case_update'):
            self.assertNotIn(hook, _HOOK_MAP)
