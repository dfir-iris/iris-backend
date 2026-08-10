#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""Error-reporter wrapper around the Sentry SDK.

`init_error_reporter_from_settings` runs once at app boot; the PUT
`/settings` handler calls `reload_error_reporter` after commit when
one of the six `error_reporting_*` fields flipped. Both are safe to
call multiple times — `sentry_sdk.init(...)` is documented as
idempotent (installs a new client into the current hub) and passing
`dsn=None` gives a null client that no-ops.

The Sentry SDK is a soft dependency: an ImportError on init is
logged and swallowed so a partial install can't take the app down.
Environments that disable error reporting outright can leave the
column blank AND omit the wheel.

The wire format is Sentry-compatible; the same code drives both
Sentry proper and GlitchTip (which re-implements the ingest API).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from typing import Optional

from app.iris_engine.observability.redaction import before_breadcrumb
from app.iris_engine.observability.redaction import make_before_send


logger = logging.getLogger(__name__)


def _fernet_decrypt(ciphertext: str) -> Optional[str]:
    """Decrypt a Fernet-wrapped DSN, or None if decrypt fails.

    Kept in a helper so a corrupted / rotated-key value returns None
    (reporter stays off) rather than raising during boot.
    """
    if not ciphertext:
        return None
    try:
        from app.iris_engine.mail.secrets import decrypt_secret
        return decrypt_secret(ciphertext)
    except Exception:
        logger.exception('Failed to decrypt error_reporting_backend_dsn')
        return None


def _coerce_sample_rate(raw: Any) -> float:
    if raw is None:
        return 1.0
    if isinstance(raw, Decimal):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def _read_settings(app) -> Optional[dict]:
    """Read the six error-reporting fields off the singleton row.

    Uses `get_srv_settings()` for the source of truth rather than the
    cached `app.config['SERVER_SETTINGS']` because init runs before
    the cache is populated on cold boot. Returns None if the DB is
    unreachable (reporter stays off).
    """
    try:
        with app.app_context():
            from app.business.server_settings import get_srv_settings
            row = get_srv_settings()
            if row is None:
                return None
            return {
                'enabled': bool(getattr(row, 'error_reporting_enabled', False)),
                'backend_dsn_ciphertext': getattr(row, 'error_reporting_backend_dsn', None),
                'frontend_dsn': getattr(row, 'error_reporting_frontend_dsn', None),
                'environment': getattr(row, 'error_reporting_environment', None),
                'sample_rate': _coerce_sample_rate(
                    getattr(row, 'error_reporting_sample_rate', None)),
                'include_user': bool(getattr(row, 'error_reporting_include_user', False)),
            }
    except Exception:
        logger.exception('Failed to load error-reporter settings')
        return None


def _do_init(dsn: Optional[str], *, environment: Optional[str],
             sample_rate: float, release: Optional[str],
             strict_frame_vars: bool) -> None:
    """Call `sentry_sdk.init` with the shared kwargs.

    Callers passing `dsn=None` get a null client — the SDK stays
    loaded but every capture is a no-op. This is the toggle-off path.
    """
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError:
        logger.warning('sentry-sdk not installed; error reporter disabled')
        return

    integrations: list[Any] = [
        FlaskIntegration(transaction_style='endpoint'),
        # `level=None` disables breadcrumb capture from stdlib logging.
        # Under gevent + psycogreen, INFO-level breadcrumb capture walks
        # frames and touches shared Sentry Hub state on every log call,
        # which yields the greenlet at unpredictable points and widens
        # the window for two greenlets to step on the same psycopg2
        # connection ("lost synchronization with server" desync). We
        # keep `event_level=ERROR` so ERROR logs still surface as
        # Sentry events — that's the important half of this integration.
        LoggingIntegration(
            level=None,
            event_level=logging.ERROR,
        ),
    ]
    try:
        from sentry_sdk.integrations.celery import CeleryIntegration
        integrations.append(CeleryIntegration())
    except ImportError:
        pass

    # SqlalchemyIntegration installs `before_cursor_execute` /
    # `after_cursor_execute` listeners on every engine. Under gevent +
    # psycogreen those listeners run in the same greenlet as the query
    # and can widen the window in which two greenlets step on the same
    # psycopg2 connection, producing the "lost synchronization with
    # server" desync. With traces_sample_rate=0 we get no spans out of
    # it anyway, so it's pure downside — opt out via
    # `disabled_integrations` (also blocks sentry-sdk's auto-enable).
    #
    # `attach_stacktrace=False` matches: on-error events already carry
    # their own traceback; the flag only adds a synthetic stack to
    # message-level captures, which under gevent means frame-walking on
    # every capture call — another yield opportunity we don't need.
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        sample_rate=sample_rate,
        traces_sample_rate=0.0,
        send_default_pii=False,
        max_breadcrumbs=30,
        attach_stacktrace=False,
        # Never walk frame locals at capture time. Sentry's default
        # serializer calls `repr()` on every value in `frame.f_locals`
        # while building the event payload. Under gevent + psycogreen a
        # `repr()` on a SQLAlchemy Cursor / Connection / Result can
        # lazy-fetch, which enters psycopg2's C wait_callback and
        # yields the greenlet — while the traceback we're serializing
        # holds a checked-out connection mid-transaction-abort. That
        # yield is where two greenlets end up sharing the same
        # psycopg2 socket ("lost synchronization with server"). Frame
        # locals stripping in `before_send` runs too late — the yield
        # already happened during serialize.
        include_local_variables=False,
        before_send=make_before_send(strict_frame_vars=strict_frame_vars),
        before_breadcrumb=before_breadcrumb,
        integrations=integrations,
        disabled_integrations=[SqlalchemyIntegration()],
    )


def _init_from_snapshot(snapshot: Optional[dict], app) -> None:
    if snapshot is None or not snapshot['enabled']:
        _do_init(None, environment=None, sample_rate=1.0, release=None,
                 strict_frame_vars=False)
        return

    dsn = _fernet_decrypt(snapshot['backend_dsn_ciphertext'])
    if not dsn:
        # Enabled but no usable DSN — treat as off. Log a warning so
        # the operator sees why nothing's being captured.
        logger.warning(
            'error_reporting_enabled=true but backend DSN is missing/undecryptable'
        )
        _do_init(None, environment=None, sample_rate=1.0, release=None,
                 strict_frame_vars=False)
        return

    _do_init(
        dsn,
        environment=snapshot['environment'],
        sample_rate=snapshot['sample_rate'],
        release=app.config.get('IRIS_VERSION'),
        # An operator who ships reports off-box should set
        # REPORTER_STRICT_FRAME_VARS=1 to drop all stack-frame locals.
        # Env-only for now — the DB-backed toggle is a nice-to-have.
        strict_frame_vars=bool(app.config.get('REPORTER_STRICT_FRAME_VARS')),
    )


def init_error_reporter_from_settings(app) -> None:
    """Init the Sentry SDK from the singleton settings row.

    Safe to call at app boot before any request has been served. Wrap
    every failure — this must never take the app down.
    """
    try:
        snapshot = _read_settings(app)
        _init_from_snapshot(snapshot, app)
    except Exception:
        logger.exception('init_error_reporter_from_settings failed')


def reload_error_reporter(app) -> None:
    """Re-init after a settings change.

    Called by the PUT `/settings` handler when any of the six
    error-reporting fields moved. `sentry_sdk.init` replaces the
    client on the current hub so subsequent captures use the fresh
    config.
    """
    init_error_reporter_from_settings(app)
