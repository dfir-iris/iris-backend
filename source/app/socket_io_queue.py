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

"""Socket.IO message queue shared by every process that emits.

gunicorn runs several workers and each one holds its own Socket.IO
clients. Without a queue, `socket_io.emit(...)` only reaches the clients
of the worker that made the call: a chat message, a notification or a
collaborative-note update pushed from worker 1 never reaches a browser
connected to worker 3, and an emit from a Celery task reaches nobody.
With the queue, every emit is published on a RabbitMQ fanout exchange
and each worker relays it to its own clients.

python-socketio's `KombuManager` does the work. `SocketIOKombuManager`
hardens three things it leaves to the caller:

  * it publishes on one connection opened in `__init__`. Under
    `gunicorn --preload` and Celery's prefork pool that object is
    created in the parent and inherited by every child, so a child that
    published on it would share the parent's socket. The connection is
    recreated the first time a new process publishes.
  * gevent runs many requests concurrently in one worker, all on that
    single connection; a lock keeps their AMQP frames from interleaving.
  * kombu's `ensure` retries forever by default, which would hang the
    request that emitted for as long as RabbitMQ is down. Retries are
    bounded; a failed publish is logged and dropped (clients on the
    emitting worker are served locally before publishing, so they
    still get the event).
"""

import os
import threading

import kombu
from socketio import KombuManager


_DISABLED_VALUES = frozenset({'none', 'off', 'false', 'disabled', '0'})

_PUBLISH_MAX_RETRIES = 1

_CONNECT_TIMEOUT_SECONDS = 2

CHANNEL = 'iris-socketio'


def socket_io_queue_url(value):
    """Return the message-queue URL to use, or None when the queue is turned off."""
    if value is None:
        return None
    value = value.strip()
    if not value or value.lower() in _DISABLED_VALUES:
        return None
    return value


class SocketIOKombuManager(KombuManager):

    def __init__(self, url, **kwargs):
        super().__init__(url, **kwargs)
        self._publisher_pid = os.getpid()
        self._publish_lock = threading.Lock()

    def _connection(self):
        return kombu.Connection(self.url, connect_timeout=_CONNECT_TIMEOUT_SECONDS,
                                **self.connection_options)

    def _producer_publish(self, connection):
        producer = connection.Producer(exchange=self._exchange(), **self.producer_options)
        return connection.ensure(producer, producer.publish,
                                 max_retries=_PUBLISH_MAX_RETRIES, interval_start=0)

    def _publish(self, data):
        # One bounded attempt instead of KombuManager's retry-then-retry:
        # the caller is a request waiting on this.
        with self._publish_lock:
            if self._publisher_pid != os.getpid():
                self.publisher_connection = self._connection()
                self._publisher_pid = os.getpid()
            try:
                self._producer_publish(self.publisher_connection)(self.json.dumps(data))
            except Exception as exc:
                self._get_logger().error(f'Socket.IO queue: cannot publish, dropping the event ({exc})')


def socket_io_queue_kwargs(url):
    """Extra `SocketIO(...)` arguments for the configured queue (empty when off)."""
    url = socket_io_queue_url(url)
    if url is None:
        return {}
    return {'client_manager': SocketIOKombuManager(url, channel=CHANNEL)}
