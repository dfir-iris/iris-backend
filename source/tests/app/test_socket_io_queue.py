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

"""Unit tests for the Socket.IO message queue wiring.

No broker is contacted: kombu connections are lazy, and publishing is
mocked at the connection.
"""

from unittest import TestCase
from unittest.mock import MagicMock
from unittest.mock import patch

from app import socket_io
from app.socket_io_queue import CHANNEL
from app.socket_io_queue import SocketIOKombuManager
from app.socket_io_queue import socket_io_queue_kwargs
from app.socket_io_queue import socket_io_queue_url


class TestSocketIOQueueUrl(TestCase):

    def test_broker_url_is_kept(self):
        self.assertEqual('amqp://rabbitmq', socket_io_queue_url(' amqp://rabbitmq '))

    def test_queue_can_be_turned_off(self):
        for value in (None, '', '  ', 'none', 'None', 'off', 'false', 'disabled', '0'):
            self.assertIsNone(socket_io_queue_url(value), value)

    def test_turned_off_queue_adds_no_socket_io_argument(self):
        self.assertEqual({}, socket_io_queue_kwargs('none'))

    def test_queue_replaces_the_default_client_manager(self):
        manager = socket_io_queue_kwargs('amqp://rabbitmq')['client_manager']
        self.assertIsInstance(manager, SocketIOKombuManager)
        self.assertEqual(CHANNEL, manager.channel)
        self.assertFalse(manager.write_only)


class TestAppSocketIO(TestCase):

    def test_app_emits_through_the_queue(self):
        # The test env points CELERY_BROKER at rabbitmq, which is the default.
        self.assertIsInstance(socket_io.server.manager, SocketIOKombuManager)


class TestSocketIOKombuManagerPublish(TestCase):

    def _manager(self):
        manager = SocketIOKombuManager('amqp://rabbitmq', channel=CHANNEL)
        manager.publisher_connection = MagicMock()
        return manager

    def test_publish_retries_are_bounded(self):
        manager = self._manager()
        manager._publish({'method': 'emit'})
        kwargs = manager.publisher_connection.ensure.call_args.kwargs
        self.assertIsNotNone(kwargs.get('max_retries'))

    def test_same_process_reuses_its_connection(self):
        manager = self._manager()
        connection = manager.publisher_connection
        manager._publish({'method': 'emit'})
        self.assertIs(connection, manager.publisher_connection)

    def test_forked_process_opens_its_own_connection(self):
        manager = self._manager()
        inherited = manager.publisher_connection
        fresh = MagicMock()
        with patch('app.socket_io_queue.os.getpid', return_value=manager._publisher_pid + 1), \
                patch.object(manager, '_connection', return_value=fresh):
            manager._publish({'method': 'emit'})
        self.assertIs(fresh, manager.publisher_connection)
        inherited.ensure.assert_not_called()
        fresh.ensure.assert_called_once()

    def test_publish_failure_does_not_raise(self):
        manager = self._manager()
        manager.publisher_connection.ensure.side_effect = OSError('broker down')
        manager._publish({'method': 'emit'})
