#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Gunicorn WSGI entrypoint.

Gevent + psycopg2 monkey-patching happens in
`scripts/gunicorn-cfg.py` at the top of that module, so patches are
in place before gunicorn's worker class imports anything (ssl,
urllib3, anyio). Doing it here would be too late without `--preload`
— gunicorn loads its worker deps before importing the app.

Patch calls are still re-run below defensively (both are idempotent)
in case this module is imported outside of gunicorn (tests, CLI
tools, ad-hoc scripts).

Gunicorn invocation: see `docker/webApp/iris-entrypoint.sh`.
"""
from gevent import monkey
monkey.patch_all()

from psycogreen.gevent import patch_psycopg
patch_psycopg()

from app import app  # noqa: F401
