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
# from the entrypoint's gunicorn command line. This file's job is
# twofold:
#
# 1. Gevent monkey-patching — MUST happen before gunicorn's worker
#    class (`GeventWebSocketWorker`) imports anything that transitively
#    pulls in `ssl` / `urllib3` / `anyio`. Without `--preload`, our
#    `wsgi.py` runs too late — gunicorn has already loaded its worker
#    dependencies by then, and gevent emits a MonkeyPatchWarning that
#    the patched ssl won't be seen by those modules. That would
#    reintroduce socket races on every HTTPS call (Sentry envelopes,
#    outbound webhooks, etc.). The gunicorn config file is the
#    earliest hook we own — imported by the arbiter before it spawns
#    any worker or imports the app.
# 2. `post_fork` hook — see its docstring below.
from gevent import monkey
monkey.patch_all()

from psycogreen.gevent import patch_psycopg
patch_psycopg()

import sys


def worker_exit(server, worker):
    sys.exit(4)


def post_fork(server, worker):
    """Rebuild fork-hostile resources after each worker forks.

    With `--preload`, gunicorn imports `wsgi:app` in the arbiter and
    each child worker inherits that already-initialized state via
    fork. Two things in there don't survive fork cleanly:

    1. SQLAlchemy engine — its connection pool holds live TCP sockets.
       Two workers reading/writing the same fd desync psycopg2 with
       "lost synchronization with server: got message type ...".
    2. Sentry SDK — `sentry_sdk.init()` spawns a `BackgroundWorker`
       OS thread that owns a `threading.Lock`. `fork()` copies memory
       but NOT threads, so the child inherits the lock in whatever
       state the parent left it (potentially held) while the thread
       itself is gone. The next Hub touch from a greenlet in the
       child can then deadlock on that orphan lock, or (worse under
       gevent) yield mid-query with the lock half-owned and let
       another greenlet step on the shared psycopg2 connection —
       same desync signature as (1), same evictor catches it, but
       the root cause is the fork not the pool.

    The `--preload` flag is load-bearing (see the entrypoint comment
    for `post_init.run()` / `db.create_all()` — dropping preload lets
    all four workers race on `CREATE TABLE` at boot), so rebuilding
    these resources post-fork is the correct fix, not `--no-preload`.
    """
    try:
        from app import db
        db.engine.dispose()
    except Exception:  # pragma: no cover — worker boot best-effort
        pass

    try:
        # Re-init Sentry so each worker gets its own fresh
        # BackgroundWorker thread + Hub. The module-level snapshot on
        # ServerSettings is what `init_error_reporter_from_settings`
        # read at arbiter import; re-reading it here keeps DSN /
        # sample_rate / environment consistent across master + workers.
        from app import app as flask_app
        from app.iris_engine.observability.reporter import (
            init_error_reporter_from_settings,
        )
        with flask_app.app_context():
            init_error_reporter_from_settings(flask_app)
    except Exception:  # pragma: no cover — worker boot best-effort
        pass
