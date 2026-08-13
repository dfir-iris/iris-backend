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

import os
from flask import Flask
from flask import g
from flask import request
from flask import session
from flask_bcrypt import Bcrypt
from flask_caching import Cache

from flask_login import LoginManager
from flask_marshmallow import Marshmallow
from flask_socketio import SocketIO
from flask_socketio import Namespace

from werkzeug.middleware.proxy_fix import ProxyFix

from app.cors import DEV_ORIGINS
from app.cors import ANY_ORIGIN
from app.cors import apply_cors_headers
from app.cors import preflight_response
from app.flask_dropzone import Dropzone
from app.configuration import Config
from app.iris_engine.tasker.celery import make_celery
from app.iris_engine.tasker.celery import set_celery_flask_context
from app.iris_engine.access_control.oidc_handler import get_oidc_client
from app.jinja_filters import register_jinja_filters
from app.models.authorization import ac_flag_match_mask
from app.db import db


class ReverseProxied(object):
    def __init__(self, flask_app):
        self._app = flask_app

    def __call__(self, environ, start_response):
        scheme = environ.get('HTTP_X_FORWARDED_PROTO', None)
        if scheme is not None:
            environ['wsgi.url_scheme'] = scheme
        return self._app(environ, start_response)


class AlertsNamespace(Namespace):
    pass


APP_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(APP_PATH, 'templates/')

bc = Bcrypt()  # flask-bcrypt
ma = Marshmallow()
celery = make_celery(__name__)
app = Flask(__name__, static_folder='../static')


def ac_current_user_has_permission(*permissions):
    """
    Return True if current user has permission
    """
    if 'permissions' not in session:
        return False

    current_user_permissions = session['permissions']
    for permission in permissions:

        if ac_flag_match_mask(current_user_permissions, permission.value):
            return True

    return False


def ac_current_user_has_manage_perms():

    if session['permissions'] != 1 and session['permissions'] & 0x1FFFFF0 != 0:
        return True
    return False


register_jinja_filters(app.jinja_env)

app.jinja_env.globals.update(user_has_perm=ac_current_user_has_permission)
app.jinja_env.globals.update(user_has_manage_perms=ac_current_user_has_manage_perms)
app.jinja_options['autoescape'] = lambda _: True
app.jinja_env.autoescape = True

app.config.from_object(Config)
app.config['timezone'] = 'Europe/Paris'
from app.post_init import PostInit

cache = Cache(app)

db.init_app(app)


# ---- Poisoned-connection eviction ---------------------------------
# Under gevent + psycogreen, a query that yields mid-result and gets
# stepped on by another greenlet can leave a psycopg2 connection in a
# desynced protocol state ("lost synchronization with server: got
# message type X" / "error with status PGRES_TUPLES_OK and no message
# from libpq"). `pool_pre_ping` doesn't catch it — the connection
# looks fine to a plain `SELECT 1`. The `handle_error` event fires on
# every DBAPI-level exception; when we see one of these signatures we
# force-invalidate the connection so the pool discards it instead of
# handing it out to the next request.
def _iris_db_error_handler(context):
    from sqlalchemy.exc import DBAPIError
    exc = context.original_exception
    if not isinstance(exc, Exception):
        return
    msg = str(exc)
    poisoned_signatures = (
        'lost synchronization with server',
        'PGRES_TUPLES_OK and no message',
        'server closed the connection unexpectedly',
        'SSL SYSCALL error',
    )
    if any(sig in msg for sig in poisoned_signatures):
        # Marking the connection invalid on the pool tells SQLAlchemy
        # to dispose it on release. Next checkout builds a fresh one.
        app.logger.warning(
            'iris.db: invalidating poisoned connection — %s',
            msg.splitlines()[0] if msg else '(no message)',
        )
        return DBAPIError.instance(
            statement=context.statement,
            params=context.parameters,
            orig=exc,
            dbapi_base_err=type(exc),
            connection_invalidated=True,
        )
    return None


with app.app_context():
    from sqlalchemy import event
    event.listen(db.engine, 'handle_error', _iris_db_error_handler)

    # ---- Greenlet-race instrumentation (opt-in) ------------------
    # Set IRIS_DEBUG_GREENLET_RACE=1 in the container env to enable.
    # When enabled, logs any time two greenlets share a psycopg2
    # connection — the direct cause of the "lost synchronization with
    # server" / "NoSuchColumnError" / "ResourceClosedError" family of
    # errors. We hook TWO layers so we catch it wherever it happens:
    #
    #   1. pool checkout/checkin — a connection can only legally be
    #      checked out by one owner at a time. If a second greenlet
    #      checks out a connection that's already checked out, the
    #      pool is broken (or someone is calling `pool._do_get` past
    #      the semaphore).
    #   2. before/after cursor execute — an owner can hold a
    #      connection but yield mid-fetch; if another greenlet issues
    #      SQL on the same DBAPI connection in the yield window, the
    #      cursor state gets scrambled (mixed column metadata → the
    #      IndexError / NoSuchColumnError signature we've been seeing).
    #
    # Also dumps `db.session` identity + scope on every checkout so
    # we can confirm whether the scoped_session is actually per-greenlet.
    # Off by default because per-cursor bookkeeping + traceback capture
    # aren't free; only turn on to hunt the race.
    if os.environ.get('IRIS_DEBUG_GREENLET_RACE') == '1':
        import traceback as _tb

        # {dbapi_conn_id: (greenlet_id, stack_str, statement_snippet)}
        _in_flight: dict[int, tuple[int, str, str]] = {}
        # {dbapi_conn_id: (greenlet_id, checkout_stack)}
        _checked_out: dict[int, tuple[int, str]] = {}

        def _greenlet_id() -> int:
            try:
                import gevent
                return id(gevent.getcurrent())
            except Exception:
                import threading as _th
                return _th.get_ident()

        def _on_checkout(dbapi_conn, conn_record, conn_proxy):
            conn_key = id(dbapi_conn)
            me = _greenlet_id()
            prev = _checked_out.get(conn_key)
            if prev and prev[0] != me:
                app.logger.error(
                    'iris.db.race[pool]: greenlet %s checked out connection %s '
                    'that greenlet %s already has checked out.\n'
                    '  prior checkout stack:\n%s\n'
                    '  new checkout stack:\n%s\n'
                    '  session id=%s scope=%s',
                    me, conn_key, prev[0], prev[1],
                    ''.join(_tb.format_stack()),
                    id(db.session()),
                    getattr(db.session, 'registry', None)
                    and db.session.registry.scopefunc()
                    if hasattr(db.session, 'registry') else '<n/a>',
                )
            _checked_out[conn_key] = (me, ''.join(_tb.format_stack()))

        def _on_checkin(dbapi_conn, conn_record):
            conn_key = id(dbapi_conn)
            _checked_out.pop(conn_key, None)
            _in_flight.pop(conn_key, None)

        def _on_before_execute(conn, cursor, statement, parameters,
                                context, executemany):
            conn_key = id(conn.connection)  # id of the DBAPI conn
            me = _greenlet_id()
            holder = _in_flight.get(conn_key)
            if holder and holder[0] != me:
                app.logger.error(
                    'iris.db.race[cursor]: greenlet %s issued SQL on '
                    'connection %s while greenlet %s is still mid-query.\n'
                    '  holder stmt: %s\n  intruder stmt: %s\n'
                    '  holder stack:\n%s\n'
                    '  intruder stack:\n%s',
                    me, conn_key, holder[0],
                    holder[2], statement[:200],
                    holder[1],
                    ''.join(_tb.format_stack()),
                )
            _in_flight[conn_key] = (me, ''.join(_tb.format_stack()),
                                     statement[:200])

        def _on_after_execute(conn, cursor, statement, parameters,
                               context, executemany):
            conn_key = id(conn.connection)
            me = _greenlet_id()
            holder = _in_flight.get(conn_key)
            if holder and holder[0] != me:
                # We finished executing but someone else is now the
                # recorded holder — they stole the connection while we
                # were yielded inside psycopg2's C-level wait_callback.
                # This is the exact shape of the desync bug.
                app.logger.error(
                    'iris.db.race[stolen]: greenlet %s finished SQL on '
                    'connection %s but greenlet %s took ownership mid-flight.\n'
                    '  our stmt: %s\n  thief stmt: %s\n'
                    '  our stack:\n%s\n  thief stack:\n%s',
                    me, conn_key, holder[0],
                    statement[:200], holder[2],
                    ''.join(_tb.format_stack()),
                    holder[1],
                )
            if holder and holder[0] == me:
                _in_flight.pop(conn_key, None)

        event.listen(db.engine, 'checkout', _on_checkout)
        event.listen(db.engine, 'checkin', _on_checkin)
        event.listen(db.engine, 'before_cursor_execute', _on_before_execute)
        event.listen(db.engine, 'after_cursor_execute', _on_after_execute)

        # ---- psycopg2-level instrumentation ---------------------------
        # SQLAlchemy events fire around `cursor.execute()` but NOT around:
        #   - `connection.rollback()` / `.commit()` (used by SA's own
        #     transaction bookkeeping — these bytes go on the same wire
        #     as user queries and the desync signatures we see happen
        #     inside `do_rollback`).
        #   - `cursor.fetchone/fetchmany/fetchall` (each fetch is a
        #     separate psycopg2 call and each can yield the greenlet
        #     under psycogreen's wait_callback).
        #   - cursors created directly from a checked-out DBAPI conn
        #     bypassing SQLAlchemy's ExecutionContext.
        #
        # psycopg2's `connection` and `cursor` are immutable C types so
        # method assignment on them fails. The supported hook is
        # `connection_factory` (used at connect time) + `cursor_factory`
        # (set on the connection instance). We install a subclass of
        # each, and each overridden method logs (greenlet_id, conn_id).
        # Any time a method starts while another greenlet is mid-call
        # on the same connection, we log a race.
        try:
            import psycopg2.extensions as _psy_ext

            # {conn_id: (greenlet_id, method_name, stack)}
            _psy_active: dict[int, tuple[int, str, str]] = {}

            def _psy_race_log(conn_key, method_name, extra=''):
                me = _greenlet_id()
                active = _psy_active.get(conn_key)
                if active and active[0] != me:
                    app.logger.error(
                        'iris.db.race[psycopg2.%s]: greenlet %s entered '
                        '%s on connection %s while greenlet %s is still '
                        'inside %s.%s\n'
                        '  prior stack:\n%s\n'
                        '  intruder stack:\n%s',
                        method_name, me, method_name, conn_key,
                        active[0], active[1],
                        f'\n  {extra}' if extra else '',
                        active[2],
                        ''.join(_tb.format_stack()),
                    )
                _psy_active[conn_key] = (
                    me, method_name, ''.join(_tb.format_stack()))
                return me

            def _psy_race_clear(conn_key, me):
                current = _psy_active.get(conn_key)
                if current and current[0] == me:
                    _psy_active.pop(conn_key, None)

            class _TracedCursor(_psy_ext.cursor):
                def _key(self):
                    conn = self.connection
                    return id(conn) if conn is not None else id(self)

                def execute(self, query, vars=None):
                    key = self._key()
                    stmt = ''
                    try:
                        stmt = (query.decode() if isinstance(query, bytes)
                                else str(query))[:200]
                    except Exception:
                        stmt = '<unrepr>'
                    me = _psy_race_log(key, 'cursor.execute',
                                       f'stmt: {stmt}')
                    try:
                        return super().execute(query, vars)
                    finally:
                        _psy_race_clear(key, me)

                def executemany(self, query, vars_list):
                    key = self._key()
                    me = _psy_race_log(key, 'cursor.executemany')
                    try:
                        return super().executemany(query, vars_list)
                    finally:
                        _psy_race_clear(key, me)

                def callproc(self, procname, parameters=None):
                    key = self._key()
                    me = _psy_race_log(key, 'cursor.callproc')
                    try:
                        return super().callproc(procname, parameters)
                    finally:
                        _psy_race_clear(key, me)

                def fetchone(self):
                    key = self._key()
                    me = _psy_race_log(key, 'cursor.fetchone')
                    try:
                        return super().fetchone()
                    finally:
                        _psy_race_clear(key, me)

                def fetchmany(self, size=None):
                    key = self._key()
                    me = _psy_race_log(key, 'cursor.fetchmany')
                    try:
                        return super().fetchmany(
                            size if size is not None else self.arraysize)
                    finally:
                        _psy_race_clear(key, me)

                def fetchall(self):
                    key = self._key()
                    me = _psy_race_log(key, 'cursor.fetchall')
                    try:
                        return super().fetchall()
                    finally:
                        _psy_race_clear(key, me)

            class _TracedConnection(_psy_ext.connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    # Default every cursor from this connection to the
                    # traced cursor unless a caller explicitly overrides.
                    self.cursor_factory = _TracedCursor

                def commit(self):
                    key = id(self)
                    me = _psy_race_log(key, 'connection.commit')
                    try:
                        return super().commit()
                    finally:
                        _psy_race_clear(key, me)

                def rollback(self):
                    key = id(self)
                    me = _psy_race_log(key, 'connection.rollback')
                    try:
                        return super().rollback()
                    finally:
                        _psy_race_clear(key, me)

                def cursor(self, *args, **kwargs):
                    # If caller passed cursor_factory=..., respect it;
                    # otherwise use our traced cursor.
                    kwargs.setdefault('cursor_factory', _TracedCursor)
                    return super().cursor(*args, **kwargs)

            # Install the connection factory on every new DBAPI
            # connection SQLAlchemy opens. Using SA's `do_connect` event
            # is the officially-supported way to intercept before
            # psycopg2.connect is called.
            @event.listens_for(db.engine, 'do_connect')
            def _use_traced_connection(dialect, conn_rec, cargs, cparams):
                cparams['connection_factory'] = _TracedConnection

            # Any existing connections in the pool were created before
            # the factory was installed. Dispose so they're recreated
            # with the traced factory on next checkout.
            db.engine.dispose()

            app.logger.warning(
                'iris.db.race: psycopg2-level instrumentation ENABLED')
        except Exception:
            app.logger.exception(
                'iris.db.race: failed to install psycopg2 instrumentation')

        app.logger.warning('iris.db.race: greenlet-race instrumentation ENABLED')

bc.init_app(app)

lm = LoginManager()  # flask-loginmanager
lm.init_app(app)  # init the login manager
ma.init_app(app)

dropzone = Dropzone(app)

set_celery_flask_context(celery, app)

# Effective browser origins for this instance: whatever the operator
# configured, plus the loopback origins the dev stack uses. `flask_cors`
# used to own the second half of that list while the `after_request`
# below owned the first, and the two disagreed — each appended its own
# `Access-Control-Allow-Origin`, and a response carrying two of them is
# rejected by every browser. One layer now, in `app/cors.py`.
#
# The wildcard swallows the dev entries: once any origin is allowed,
# enumerating six of them adds nothing.
_allowed_origins = list(app.config['IRIS_ALLOWED_ORIGINS'])
if ANY_ORIGIN not in _allowed_origins:
    _allowed_origins += [o for o in DEV_ORIGINS if o not in _allowed_origins]
app.config['IRIS_ALLOWED_ORIGINS'] = _allowed_origins

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
#app.wsgi_app = store.wsgi_middleware(app.wsgi_app)

# `cors_allowed_origins` is narrowed from the historical `'*'` — a
# wide-open handshake acceptor lets any cross-origin page complete a
# WS upgrade against a victim's `SameSite=Lax` session cookie, which
# with the chatbot in place becomes an LLM-token-burning attack. It
# takes the same list the HTTP CORS layer uses (see `after_request`
# below), so an instance answering on several hostnames accepts a
# handshake from each of them rather than only the first. Falls back
# to `'*'` only if the config value is unset (existing dev workflow).
#
# `async_mode` is set from the gunicorn side: `source/wsgi.py`
# monkey-patches gevent before importing `app`, so `sys.modules` has
# `gevent` when we get here — we force `async_mode='gevent'` for the
# real server. The Celery worker imports `app.celery` WITHOUT the
# wsgi.py shim, so gevent isn't in its `sys.modules`; Flask-SocketIO
# would ValueError on `async_mode='gevent'` in that path. Detect and
# fall back to auto (threading) — Celery doesn't actually serve
# sockets, this is just so module init doesn't blow up.
import sys as _sys
if 'gevent' in _sys.modules:
    _socket_async_mode = 'gevent'
else:
    _socket_async_mode = None  # let Flask-SocketIO pick
# python-socketio takes either the literal '*' or a list of origins;
# handing it a one-element list containing '*' is NOT the same thing
# there, so unwrap the wildcard.
if ANY_ORIGIN in _allowed_origins:
    _socket_allowed_origins = ANY_ORIGIN
else:
    _socket_allowed_origins = _allowed_origins
_socket_kwargs = {'cors_allowed_origins': _socket_allowed_origins}
if _socket_async_mode is not None:
    _socket_kwargs['async_mode'] = _socket_async_mode
socket_io = SocketIO(app, **_socket_kwargs)

alerts_namespace = AlertsNamespace('/alerts')
socket_io.on_namespace(alerts_namespace)

# Chatbot `/chat` namespace. Registered here rather than at blueprint
# import time because SocketIO namespaces bind to the `socket_io`
# instance created just above, so import order matters. See
# `app/blueprints/rest/v2/case_chat/namespace.py`.
from app.blueprints.rest.v2.case_chat.namespace import register_chat_namespace
register_chat_namespace()

oidc_client = None
if app.config.get('AUTHENTICATION_TYPE') == 'oidc':
    oidc_client = get_oidc_client(app.config, app.logger)
from app.views import register_blueprints
from app.views import load_user
from app.views import load_user_from_request


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.close()
    g.pop('auth_user', None)
    g.pop('auth_token_user_id', None)
    g.pop('auth_user_permissions', None)


@app.before_request
def before_request_cors():
    # Answer preflights here rather than letting them reach a view.
    # Flask's automatic OPTIONS response still runs every before_request
    # hook, so an auth check can reject a preflight — and the browser
    # reports that as an opaque CORS failure with no hint that the
    # credentials on the real request would have been fine. Returns
    # None for everything that is not a preflight from an allowed
    # origin, which leaves normal dispatch untouched.
    return preflight_response(request, app.config['IRIS_ALLOWED_ORIGINS'])


@app.after_request
def after_request(response):
    return apply_cors_headers(response, request, app.config['IRIS_ALLOWED_ORIGINS'])


# Request-ID middleware BEFORE blueprints so every route (including
# the 404/500 error handlers) has an id bound on `flask.g` and
# echoed on the response as `X-Request-Id`.
from app.iris_engine.observability.request_id import register_request_id_middleware
register_request_id_middleware(app)

register_blueprints(app)

# Serialize `post_init.run()` across workers via a Postgres advisory
# lock. Without `--preload` each of the 4 workers imports `wsgi:app`
# independently and would otherwise race on `CREATE TABLE IF NOT
# EXISTS`, admin-user seeding, base-data seeding, etc. `post_init` is
# already idempotent (it explicitly logs "Module already exists" when
# it hits a row that another run created), so the blocking-lock
# pattern is correct: the first worker runs it, the others block on
# `pg_advisory_lock`, unblock when the first releases, then re-run
# post_init as a no-op. Lock key is an arbitrary constant chosen to
# be recognizable in `pg_locks` — `0x69726973` is 'iris' in ASCII.
_POST_INIT_LOCK_KEY = 0x69726973
try:
    from sqlalchemy import text as _sa_text
    with app.app_context():
        with db.engine.connect() as _lock_conn:
            _lock_conn.execute(
                _sa_text('SELECT pg_advisory_lock(:k)'),
                {'k': _POST_INIT_LOCK_KEY},
            )
            try:
                post_init = PostInit(app)
                post_init.run()
            finally:
                _lock_conn.execute(
                    _sa_text('SELECT pg_advisory_unlock(:k)'),
                    {'k': _POST_INIT_LOCK_KEY},
                )
                _lock_conn.commit()

except Exception as e:
    app.logger.exception('Post init failed. IRIS not started')
    raise e

# Error reporter init runs AFTER PostInit — that's what guarantees
# the singleton ServerSettings row exists (PostInit seeds it on cold
# boot). Reads the six error_reporting_* fields and calls
# sentry_sdk.init(...) if enabled; else installs a null client. Any
# failure here is logged and swallowed — the reporter is optional
# and must never block boot.
from app.iris_engine.observability.reporter import init_error_reporter_from_settings
init_error_reporter_from_settings(app)

lm.user_loader(load_user)
lm.request_loader(load_user_from_request)

from app.blueprints.socket_io_event_handlers.case_event_handlers import register_case_event_handlers
from app.blueprints.socket_io_event_handlers.case_notes_event_handlers import register_notes_event_handlers
from app.blueprints.socket_io_event_handlers.update_event_handlers import register_update_event_handlers
from app.blueprints.socket_io_event_handlers.notification_event_handlers import register_notification_socket_handlers
from app.blueprints.socket_io_event_handlers.collab_event_handlers import register_collab_socket_handlers

register_case_event_handlers()
register_notes_event_handlers()
register_update_event_handlers()
register_notification_socket_handlers()
register_collab_socket_handlers()
