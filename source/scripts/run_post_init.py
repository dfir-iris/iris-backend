#!/usr/bin/env python3
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

"""One-shot database bootstrap.

Runs `post_init` — create database, `db.create_all()`, alembic upgrade,
base-data seeding, first admin user, demo data — exactly once, then
exits. The entrypoint invokes this before starting gunicorn.

Why a separate process: `post_init.run()` used to sit at
`app/__init__.py` module scope. Gunicorn runs without `--preload` (see
`scripts/gunicorn-cfg.py` for why), so every worker imports the app
independently and each one re-ran the whole thing — 4 full passes per
boot, serialized by an advisory lock, the last 3 pure no-ops. That is
harmless but slow, and it turns one failure into 4 interleaved
tracebacks.

Deliberately NOT gevent-patched, unlike `wsgi.py`: this is a plain
synchronous process with no server loop, so it wants ordinary blocking
psycopg2. Monkey-patching exists for the greenlet server and buys
nothing here.

Run:
    cd source && python -m scripts.run_post_init

Must run with `source/` as the working directory — post_init loads
`app/alembic.ini` by relative path. That is the container's WORKDIR
(/iriswebapp), so the entrypoint needs no `cd`.

Exit codes: 0 on success, 1 on failure (the traceback is already
logged by `run_post_init`).
"""
import sys

from app import run_post_init


if __name__ == '__main__':
    try:
        run_post_init()
    except Exception:
        # Already logged with a full traceback by run_post_init().
        sys.exit(1)

    sys.exit(0)
