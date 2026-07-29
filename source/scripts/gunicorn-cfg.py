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
# Serving flags (bind, workers, timeout, worker-class, log-level) come
# from the entrypoint's gunicorn command line. This file exists to
# hang the `post_fork` hook off — see the docstring on `post_fork`.
import sys


def worker_exit(server, worker):
    sys.exit(4)


def post_fork(server, worker):
    """Dispose the SQLAlchemy engine after each worker forks.

    With `--preload`, gunicorn imports `wsgi:app` in the arbiter and
    the resulting SQLAlchemy engine's connection pool is inherited by
    every child worker via fork. Those TCP sockets are shared — two
    workers reading/writing the same connection produces the psycopg2
    "lost synchronization with server: got message type ..." fatal.
    Disposing the pool here forces each worker to lazily open its own
    fresh connections, which is the standard gunicorn+SQLAlchemy
    +preload recipe.

    The `--preload` flag is load-bearing (see the entrypoint comment
    for `post_init.run()` / `db.create_all()` — dropping preload lets
    all four workers race on `CREATE TABLE` at boot), so the pool
    dispose is the correct fix, not `--no-preload`.
    """
    try:
        from app import db
        db.engine.dispose()
    except Exception:  # pragma: no cover — worker boot best-effort
        pass
