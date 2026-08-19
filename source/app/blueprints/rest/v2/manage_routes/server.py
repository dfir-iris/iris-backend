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

"""v2 endpoints for server-wide settings + the DB backup trigger.

Layout mirrors the rest of the v2 manage surface: a single
`ServerOperations` class, thin routes that delegate.

`PUT /server/settings` accepts a partial body — only the fields the
admin actually changed need to be sent. The full row is returned on
success so the UI can refresh in one round-trip.

`POST /server/backups/db` is the v2 replacement for the legacy GET
`/manage/server/backups/make-db`. The legacy GET stays for any
external automation calling it today; the v2 route is POST because
backup is a side-effecting action that shouldn't ride on GET.
"""

import marshmallow
from functools import wraps

from flask import Blueprint
from flask import Response
from flask import request

from app import app
from app import celery
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.rest.api_doc import api_doc
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_success
from app.business.server_settings import get_server_settings_as_dict
from app.business.server_settings import get_srv_settings
from app.db import db
from app.iris_engine.backup.backup import backup_iris_db
from app.iris_engine.demo_builder import demo_mode_blocks_mfa
from app.iris_engine.demo_builder import demo_mode_restricts_server_settings
from app.iris_engine.demo_builder import is_demo_mode_enabled
from app.iris_engine.mail.outbound import mail_send_system
from app.iris_engine.mail.secrets import encrypt_secret
from app.iris_engine.llm.reload import reload_llm_client
from app.iris_engine.observability.reporter import reload_error_reporter
from app.iris_engine.updater.updater import remove_periodic_update_checks
from app.iris_engine.updater.updater import setup_periodic_update_checks
from app.iris_engine.utils.tracker import track_activity
from app.models.authorization import Permissions
from app.schema.marshables import ServerSettingsSchema
from dictdiffer import diff


# Mail passwords are held as ciphertext in the DB; the schema marks
# these fields `load_only=True` so a GET never leaks even the
# ciphertext. Instead the read path emits *_password_set booleans so
# the SPA can render a "•••••" placeholder for a set field vs an
# empty input for an unset one.
_MAIL_PASSWORD_FIELDS = ('mail_smtp_password', 'mail_imap_password')

# Same masking treatment for the backend DSN of the error reporter.
# The frontend DSN is not in this list — it round-trips to the SPA on
# purpose so the browser can init the Sentry SDK against it.
_ERROR_REPORTING_SECRET_FIELDS = ('error_reporting_backend_dsn',)

# Any change to one of these fields triggers `reload_error_reporter`
# after commit — snapshot before the schema load and compare, same
# shape as the mail-interval snapshot above.
_ERROR_REPORTING_TRACKED_FIELDS = (
    'error_reporting_enabled',
    'error_reporting_backend_dsn',
    'error_reporting_frontend_dsn',
    'error_reporting_environment',
    'error_reporting_sample_rate',
    'error_reporting_include_user',
)

# MCP settings live on the same row but need no reload side-effect —
# the transport (see `app.blueprints.rest.v2.mcp.transport`) reads the
# settings on every request, so flipping `mcp_enabled` and friends
# takes effect immediately. Nothing to snapshot/compare/reload here;
# the `dictdiffer` block below already emits an activity-log entry
# naming the changed MCP fields alongside everything else.

# Chatbot secret — Fernet-encrypted at rest, same treatment as the
# error-reporting backend DSN and mail passwords.
_CHATBOT_SECRET_FIELDS = ('chatbot_api_key',)

# Any change to one of these triggers `reload_llm_client(app)` after
# commit. `reload_llm_client` is a no-op today (the provider factory
# reads settings on every call) but the hook is here so future caching
# work stays wired the same way.
_CHATBOT_TRACKED_FIELDS = (
    'chatbot_enabled',
    'chatbot_provider',
    'chatbot_api_key',
    'chatbot_model',
    'chatbot_base_url',
    'chatbot_max_turns_per_conversation',
    'chatbot_max_tool_calls_per_turn',
    'chatbot_auto_execute_read_tools',
    'chatbot_auto_approve_write_tools',
    'chatbot_daily_token_budget_per_user',
    'chatbot_daily_token_budget_org',
    'chatbot_redact_ips',
    'chatbot_redact_emails',
    'chatbot_redact_hashes',
)


def _mail_password_flags(settings) -> dict:
    """Compact `{<field>_set: bool}` map for the mail password fields."""
    return {
        f'{field}_set': bool(getattr(settings, field, None))
        for field in _MAIL_PASSWORD_FIELDS
    }


def _error_reporting_flags(settings) -> dict:
    """Compact `{<field>_set: bool}` map for the masked error-reporting fields."""
    return {
        f'{field}_set': bool(getattr(settings, field, None))
        for field in _ERROR_REPORTING_SECRET_FIELDS
    }


def _encrypt_mail_passwords_in_body(body: dict) -> None:
    """In-place: wrap any plaintext mail password with Fernet.

    Called before the Marshmallow schema load so the encrypted string
    is what actually lands in the DB. An empty-string value means the
    admin wants to CLEAR the password (map to None). Missing keys are
    left alone — partial update, keep the current ciphertext.
    """
    for field in _MAIL_PASSWORD_FIELDS:
        if field not in body:
            continue
        raw = body[field]
        if raw is None or raw == '':
            body[field] = None
            continue
        body[field] = encrypt_secret(raw)


def _encrypt_error_reporting_secrets_in_body(body: dict) -> None:
    """In-place: Fernet-wrap the backend DSN — same rules as mail passwords."""
    for field in _ERROR_REPORTING_SECRET_FIELDS:
        if field not in body:
            continue
        raw = body[field]
        if raw is None or raw == '':
            body[field] = None
            continue
        body[field] = encrypt_secret(raw)


def _encrypt_chatbot_secrets_in_body(body: dict) -> None:
    """In-place: Fernet-wrap the chatbot API key — same rules as above."""
    for field in _CHATBOT_SECRET_FIELDS:
        if field not in body:
            continue
        raw = body[field]
        if raw is None or raw == '':
            body[field] = None
            continue
        body[field] = encrypt_secret(raw)


def _chatbot_flags(settings) -> dict:
    """Compact `{<field>_set: bool}` map for the masked chatbot fields."""
    return {
        f'{field}_set': bool(getattr(settings, field, None))
        for field in _CHATBOT_SECRET_FIELDS
    }


def _snapshot_chatbot(settings) -> dict:
    return {field: getattr(settings, field, None)
            for field in _CHATBOT_TRACKED_FIELDS}


def _chatbot_changed(before: dict, settings) -> bool:
    return any(
        before[field] != getattr(settings, field, None)
        for field in _CHATBOT_TRACKED_FIELDS
    )


def _redact_secrets_in_changes(changes: list) -> list:
    """Replace secret values in the diff with a sentinel.

    `changes` feeds directly into `track_activity` which becomes an
    audit-log row — so a raw DSN would land in cleartext there. Backend
    DSN is load-only (never in the dumped baseline), so the diff shape
    is a `add`/`change` where the new-value side needs masking. Frontend
    DSN also gets masked in the activity log even though it round-trips
    in the API response, because an activity-log row is more durable and
    more widely-read than a single API response.
    """
    redacted_keys = {
        'error_reporting_backend_dsn',
        'error_reporting_frontend_dsn',
        'chatbot_api_key',
    }
    out = []
    for entry in changes:
        # entry has the shape `{key: new_value}` — see the caller.
        redacted_entry = {}
        for key, value in entry.items():
            if key in redacted_keys and value:
                redacted_entry[key] = '<changed>'
            else:
                redacted_entry[key] = value
        out.append(redacted_entry)
    return out


def demo_mode_owner_only(view):
    """403 the whole server-settings surface for non-owners in demo mode.

    On a demo instance every visitor holds `server_administrator`, so
    that permission alone doesn't protect the SMTP credentials, the
    error-reporting DSNs, the chatbot API key or the backup trigger
    sitting behind these routes. Demo mode narrows them to the instance
    owner (`DEMO_MODE_OWNER_USER_ID` in `app.iris_engine.demo_builder`);
    outside demo mode the decorator is inert.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if demo_mode_restricts_server_settings():
            return response_api_error(
                'Server settings are not available in demo mode', status=403
            )
        return view(*args, **kwargs)
    return wrapper


def _apply_demo_mode_overrides(settings_dump: dict) -> dict:
    """Report the *effective* policy, not the stored one.

    Demo mode ignores `enforce_mfa` at login time (see
    `app.business.auth.mfa_is_enforced`), so echoing back whatever the
    row happens to hold would show the owner a toggle that claims MFA
    is on while every login sails past it. `demo_mode` rides along so
    the SPA can disable the control and say why.
    """
    settings_dump['demo_mode'] = is_demo_mode_enabled()
    if demo_mode_blocks_mfa():
        settings_dump['enforce_mfa'] = False
    return settings_dump


def _snapshot_error_reporting(settings) -> dict:
    """Read the six error-reporting fields off the settings row.

    Used before the schema load so we can compare pre/post and only
    invoke `reload_error_reporter` when something actually changed.
    """
    return {field: getattr(settings, field, None)
            for field in _ERROR_REPORTING_TRACKED_FIELDS}


def _error_reporting_changed(before: dict, settings) -> bool:
    return any(
        before[field] != getattr(settings, field, None)
        for field in _ERROR_REPORTING_TRACKED_FIELDS
    )


class ServerOperations:
    def __init__(self):
        self._schema = ServerSettingsSchema()

    # ----- Authentication ------------------------------------------

    @staticmethod
    def get_authentication_settings():
        try:
            auth_requirements = {
                "oidc_enabled": app.config.get("AUTHENTICATION_TYPE") == "oidc",
                # Demo mode forces MFA off — the login page must not
                # offer a second factor it will never be able to honour.
                "mfa_enabled": False if demo_mode_blocks_mfa() else app.config.get("MFA_ENABLED"),
                "local_fallback_enabled": bool(app.config.get("AUTHENTICATION_LOCAL_FALLBACK")),
            }
            return response_api_success(auth_requirements)
        except Exception as e:
            return response_api_error("Data error", data=str(e))

    # ----- Server settings ----------------------------------------

    def read_settings(self) -> Response:
        """Return the full settings row + a small `versions` block.

        The version block isn't on `ServerSettings`; it's read straight
        from `app.config` + the alembic head. Bundling the two lets the
        page render its read-only top-of-page strip without a second
        round-trip.

        Mail passwords are load-only on the schema so the dump never
        includes them; we attach `*_password_set` booleans instead so
        the SPA can render placeholder inputs for set-but-hidden
        fields.
        """
        settings = get_srv_settings()
        from app.business.server_settings import get_alembic_revision

        settings_dump = self._schema.dump(settings)
        settings_dump.update(_mail_password_flags(settings))
        settings_dump.update(_error_reporting_flags(settings))
        settings_dump.update(_chatbot_flags(settings))
        _apply_demo_mode_overrides(settings_dump)

        return response_api_success({
            'settings': settings_dump,
            'versions': {
                'iris_version': app.config.get('IRIS_VERSION'),
                'api_min': app.config.get('API_MIN_VERSION'),
                'api_max': app.config.get('API_MAX_VERSION'),
                'module_interface_min': app.config.get('MODULES_INTERFACE_MIN_VERSION'),
                'module_interface_max': app.config.get('MODULES_INTERFACE_MAX_VERSION'),
                'db_revision': get_alembic_revision(),
            },
        })

    def update_settings(self) -> Response:
        """Partial-update the singleton settings row.

        The schema is loaded with `partial=True` so the admin can send
        only the fields they changed. We compute a diff against the
        pre-update dump so the activity-log entry mentions exactly
        which keys moved — useful when chasing "who turned off MFA?".
        Mirrors the legacy `/manage/settings/update` behaviour 1:1
        (including the periodic-update-check side effect when the
        `enable_updates_check` flag flips).
        """
        if not request.is_json:
            return response_api_error('Invalid request')

        body = request.get_json() or {}

        # Demo mode owns the MFA policy: shared accounts can't carry a
        # second factor, so `enforce_mfa` is pinned off and refusing the
        # write is more honest than accepting one the login path ignores.
        if demo_mode_blocks_mfa() and 'enforce_mfa' in body:
            if body.get('enforce_mfa'):
                return response_api_error('MFA cannot be enabled in demo mode')
            # A no-op `enforce_mfa: false` from a full-body client is
            # harmless — drop the key rather than fail the whole PUT.
            body.pop('enforce_mfa')

        settings = get_srv_settings()
        original_update_check = settings.enable_updates_check

        # Wrap plaintext mail passwords in Fernet BEFORE the schema
        # load — the DB column should never hold a plaintext value.
        _encrypt_mail_passwords_in_body(body)
        _encrypt_error_reporting_secrets_in_body(body)
        _encrypt_chatbot_secrets_in_body(body)
        # Snapshot mail interval so we can nudge the beat scheduler
        # if the operator changes it below.
        old_imap_interval = settings.mail_imap_poll_interval_sec
        old_imap_enabled = settings.mail_imap_enabled
        error_reporting_before = _snapshot_error_reporting(settings)
        chatbot_before = _snapshot_chatbot(settings)

        try:
            original_dump = self._schema.dump(settings)
            differences = list(diff(original_dump, body))
            changes = [
                {d[1]: d[2]} for d in differences if d[0] == 'change'
            ]
            # DSN values must never land in the activity log in clear.
            changes = _redact_secrets_in_changes(changes)
            updated = self._schema.load(body, instance=settings, partial=True)
            db.session.commit()

            # Periodic update-check Celery task is added/removed only
            # when the toggle actually flips. Reading the cached value
            # *before* the load+commit is critical — `updated` is the
            # same instance as `settings`, so its attribute is already
            # the new value once load() returns.
            if original_update_check != updated.enable_updates_check:
                if updated.enable_updates_check:
                    setup_periodic_update_checks(celery)
                else:
                    remove_periodic_update_checks()

            # If the IMAP interval or enabled flag flipped, refresh
            # the beat schedule so the next tick uses the new value
            # without a worker restart.
            if (updated.mail_imap_poll_interval_sec != old_imap_interval
                    or updated.mail_imap_enabled != old_imap_enabled):
                try:
                    from app.iris_engine.mail.inbound import _register_mail_beat_schedule
                    _register_mail_beat_schedule(celery)
                except Exception:
                    app.logger.exception('Failed to refresh mail beat schedule')

            # Reload the error reporter only when one of its fields
            # actually changed. `sentry_sdk.init` is idempotent — it
            # installs a new client on the current hub, so subsequent
            # captures pick up the fresh DSN/environment/sample-rate.
            if _error_reporting_changed(error_reporting_before, updated):
                reload_error_reporter(app)

            # Chatbot reload is a no-op today (the provider factory
            # re-reads settings on every call) but the hook is here so
            # a future cached provider only needs one place to grow the
            # invalidation logic.
            if _chatbot_changed(chatbot_before, updated):
                reload_llm_client(app)

            track_activity(f'Server settings updated: {changes}', ctx_less=True)
            # Re-cache the dump on app.config so other code paths that
            # read `app.config['SERVER_SETTINGS']` see the new values
            # without an extra DB hit. The legacy route did the same.
            settings_dump = self._schema.dump(updated)
            settings_dump.update(_mail_password_flags(updated))
            settings_dump.update(_error_reporting_flags(updated))
            settings_dump.update(_chatbot_flags(updated))
            app.config['SERVER_SETTINGS'] = settings_dump
            # Overrides are applied to a copy: the cached dict must keep
            # the stored values so nothing downstream mistakes a demo
            # presentation tweak for a real setting.
            return response_api_success(_apply_demo_mode_overrides(dict(settings_dump)))

        except marshmallow.exceptions.ValidationError as exc:
            return response_api_error('Data error', data=exc.messages)

    # ----- Mail test-send ------------------------------------------

    @staticmethod
    def send_test_mail() -> Response:
        """Send a probe mail via the current SMTP config.

        Body: `{"to": "user@example.com"}`. Runs synchronously (not
        via the Celery task) so the admin sees the outcome in the
        same request — this is a diagnostic path, not the normal
        delivery path.
        """
        body = request.get_json(silent=True) or {}
        to_addr = (body.get('to') or '').strip()
        if not to_addr or '@' not in to_addr:
            return response_api_error('to must be a valid email address')
        try:
            delivered = mail_send_system(
                [to_addr],
                subject='[IRIS] SMTP test message',
                body='This is a test email from IRIS. '
                     'If you received it, your SMTP configuration works.\n',
            )
        except Exception as exc:
            return response_api_error('SMTP delivery failed', data=str(exc))
        if not delivered:
            return response_api_error(
                'SMTP is not configured or is disabled — check the mail '
                'section on the Server Settings page.'
            )
        track_activity(f'Sent SMTP test email to {to_addr}', ctx_less=True)
        return response_api_success({'delivered': True, 'to': to_addr})

    # ----- Database backup -----------------------------------------

    @staticmethod
    def make_db_backup() -> Response:
        """Trigger a synchronous Postgres dump.

        Returns the log lines from the dump runner on success so the
        admin can see what got backed up where. On failure the same
        log lines come back as `data` on the error envelope.
        """
        has_error, logs = backup_iris_db()
        if has_error:
            return response_api_error('Backup failed', data=logs)
        return response_api_success({'logs': logs})


server_blueprint = Blueprint("server_rest_v2", __name__, url_prefix="/server")

server_operations = ServerOperations()


@server_blueprint.get("/authentication-settings")
@api_doc(tags=['ManageServer'], summary='Get authentication settings')
def server_get_authsettings() -> Response:
    return server_operations.get_authentication_settings()


@server_blueprint.get('/settings')
@ac_api_requires(Permissions.server_administrator)
@demo_mode_owner_only
@api_doc(tags=['ManageServer'], summary='Get server settings')
def server_get_settings() -> Response:
    return server_operations.read_settings()


@server_blueprint.put('/settings')
@ac_api_requires(Permissions.server_administrator)
@demo_mode_owner_only
@api_doc(request=ServerSettingsSchema, response=ServerSettingsSchema,
         tags=['ManageServer'], summary='Update server settings')
def server_put_settings() -> Response:
    return server_operations.update_settings()


@server_blueprint.post('/backups/db')
@ac_api_requires(Permissions.server_administrator)
@demo_mode_owner_only
@api_doc(tags=['ManageServer'], summary='Trigger a database backup')
def server_make_db_backup() -> Response:
    return server_operations.make_db_backup()


@server_blueprint.post('/mail/test-send')
@ac_api_requires(Permissions.server_administrator)
@demo_mode_owner_only
@api_doc(tags=['ManageServer'], summary='Send an SMTP test email')
def server_send_test_mail() -> Response:
    return server_operations.send_test_mail()


# Silence the unused-import linter — `get_server_settings_as_dict` is
# re-exported here so any future v2 route that needs the cached dict
# (rather than the ORM row) finds it under the conventional name.
_ = get_server_settings_as_dict
