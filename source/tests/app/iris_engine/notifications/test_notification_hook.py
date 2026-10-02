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

"""`notify()` hands every persisted notification to the
`on_postload_notification_create` module hook. Tested without any
Flask / DB context.
"""

from datetime import datetime
from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.iris_engine.notifications.service import notify

_SERVICE = 'app.iris_engine.notifications.service'
_HOOK = 'app.iris_engine.module_handler.module_handler.call_modules_hook'


class _FakeNotification:

    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.id = 7
        self.created_at = datetime(2026, 10, 1)


class TestNotificationHook(TestCase):

    def _notify(self, channels, hook=None):
        with patch(f'{_SERVICE}._resolve_channels', return_value=channels), \
                patch(f'{_SERVICE}.Notification', _FakeNotification), \
                patch(f'{_SERVICE}.db'), \
                patch(f'{_SERVICE}._safe_socket_emit'), \
                patch(_HOOK, hook or MagicMock()) as called:
            notification = notify(3, 'mention', 'You were mentioned', body='hello')
        return notification, called

    def test_notify_should_call_the_hook_with_the_persisted_notification(self):
        notification, called = self._notify({'in_app': True, 'email': False})

        called.assert_called_once_with('on_postload_notification_create', data=notification)

    def test_notify_should_pass_the_recipient_to_the_hook(self):
        _, called = self._notify({'in_app': True, 'email': False})

        self.assertEqual(3, called.call_args.kwargs['data'].user_id)

    def test_notify_should_not_call_the_hook_when_in_app_is_disabled(self):
        # No row is written, so there is nothing to hand to modules —
        # the same condition the email hand-off hangs off.
        _, called = self._notify({'in_app': False, 'email': True})

        called.assert_not_called()

    def test_notify_should_not_call_the_hook_when_every_channel_is_disabled(self):
        _, called = self._notify({'in_app': False, 'email': False})

        called.assert_not_called()

    def test_notify_should_return_the_notification_when_the_hook_fails(self):
        def _raising_hook(*args, **kwargs):
            raise RuntimeError('module exploded')

        notification, _ = self._notify({'in_app': True, 'email': False}, _raising_hook)

        self.assertEqual('You were mentioned', notification.title)
