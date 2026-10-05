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

"""Built-in hook listeners run inside `call_modules_hook` itself, so every
caller — whichever way it imported the function — reaches them.
"""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app.iris_engine.module_handler import module_handler

_MH = 'app.iris_engine.module_handler.module_handler'


class TestBuiltinHookListeners(TestCase):

    def setUp(self):
        self.registered = list(module_handler._builtin_hook_listeners)
        self.addCleanup(self._restore)
        module_handler._builtin_hook_listeners.clear()

    def _restore(self):
        module_handler._builtin_hook_listeners[:] = self.registered

    def _call(self, hook='on_postload_ioc_create', data='payload', **kwargs):
        with patch(f'{_MH}.IrisHook') as iris_hook, patch(f'{_MH}.IrisModuleHook') as module_hook:
            iris_hook.query.filter.return_value.first.return_value = MagicMock(id=1)
            module_hook.query.with_entities.return_value.filter.return_value.join.return_value.all.return_value = []
            return module_handler.call_modules_hook(hook, data, caseid=4, **kwargs)

    def test_registered_listener_should_receive_the_hook(self):
        listener = MagicMock()
        module_handler.register_builtin_hook_listener(listener)
        self._call()
        listener.assert_called_once_with('on_postload_ioc_create', 'payload', caseid=4, hook_ui_name=None)

    def test_registering_twice_should_call_once(self):
        listener = MagicMock()
        module_handler.register_builtin_hook_listener(listener)
        module_handler.register_builtin_hook_listener(listener)
        self._call()
        self.assertEqual(1, listener.call_count)

    def test_failing_listener_should_not_break_the_hook_or_the_others(self):
        failing = MagicMock(side_effect=RuntimeError('boom'))
        other = MagicMock()
        module_handler.register_builtin_hook_listener(failing)
        module_handler.register_builtin_hook_listener(other)
        self.assertEqual('payload', self._call())
        other.assert_called_once()

    def test_targeted_module_call_should_skip_listeners(self):
        listener = MagicMock()
        module_handler.register_builtin_hook_listener(listener)
        self._call(module_name='some_module')
        listener.assert_not_called()

    def test_app_import_should_register_notifications_and_webhooks(self):
        from app.iris_engine.notifications.hook_listeners import _on_hook
        from app.iris_engine.webhooks.dispatch import webhooks_on_hook
        self.assertIn(_on_hook, self.registered)
        self.assertIn(webhooks_on_hook, self.registered)
