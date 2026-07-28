#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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
import sys

bind = 'unix:sock'
workers = 4
# `gevent` worker class is required for Flask-SocketIO's `async_mode='gevent'`,
# which the chatbot's LLM streaming needs so a 30-second token stream doesn't
# occupy a whole sync worker for its full lifetime. Also improves throughput
# for `/notifications` and `/collab` namespaces — under sync workers those
# were holding a worker thread per open SocketIO connection anyway. IRIS is
# IO-bound, so the cooperative-concurrency model is a strict win.
worker_class = 'gevent'
worker_connections = 200
accesslog = '-'
loglevel = 'warning'
errorlog = '/var/log/iris/errors.log'
timeout = 3000


def worker_exit(server, worker):
    sys.exit(4)
