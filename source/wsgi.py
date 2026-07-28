#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Gunicorn WSGI entrypoint — does gevent monkey-patching FIRST.

Everything under `app.*` transitively imports `socket`, `ssl`,
`threading`, `time`, and `requests`. `gevent.monkey.patch_all()` MUST
run before those imports so the patched versions land in `sys.modules`
and every downstream consumer picks them up. That means the
patch-and-import dance can't happen inside `app/__init__.py` — Flask,
Flask-SocketIO, flask_login, etc. would already have imported the
blocking stdlib by then. Hence this thin shim: patch, then import.

`psycogreen.gevent.patch_psycopg()` is REQUIRED alongside the stdlib
patch: psycopg2 is a blocking C extension that gevent's socket hook
can't intercept, so without psycogreen every SQLAlchemy query would
serialize the whole worker's greenlets. psycogreen wraps psycopg2's
`wait_callback` in a `gevent.socket.wait_read` so the greenlet yields
during query execution.

Gunicorn invocation (see `docker/webApp/iris-entrypoint.sh`):

    gunicorn wsgi:app --worker-class gevent --worker-connections 1000 \\
      -w 4 --bind 0.0.0.0:8000 --timeout 180 --log-level=info
"""
from gevent import monkey
monkey.patch_all()

from psycogreen.gevent import patch_psycopg
patch_psycopg()

# Only import the Flask app AFTER patching is complete. Any `import app`
# above this line would defeat the whole point of the shim.
from app import app  # noqa: F401
