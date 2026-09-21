from sqlalchemy import text

from app.db import db
from app.models.models import ServerSettings
from app.schema.marshables import ServerSettingsSchema


def get_srv_settings():
    return ServerSettings.query.first()


def get_server_settings_as_dict():
    srv_settings = ServerSettings.query.first()
    if srv_settings:

        sc = ServerSettingsSchema()
        return sc.dump(srv_settings)

    return {}


def get_server_settings_enforce_mfa() -> bool:
    """Read the MFA policy off the settings row. Deliberately uncached.

    `app.config` is per-process and the application runs under
    `gunicorn -w 4`, so any cached copy diverges across workers the moment
    an admin flips the toggle: the `PUT` refreshes the one worker that
    served it and the rest keep answering with whatever they read first.
    A worker still holding `False` mints `mfa_required=False`, which
    `generate_auth_tokens` turns into `mfa_verified=True` — a fully
    verified token on an MFA-enforced server. One indexed single-column
    SELECT on the auth path is the price of not having that.

    No row yields False, matching `get_server_settings_as_dict`'s `{}`.
    `post_init` creates the row before gunicorn binds, so that case is
    unreachable in practice.
    """
    row = db.session.query(ServerSettings.enforce_mfa).first()
    return bool(row[0]) if row else False


def get_alembic_revision():
    with db.engine.connect() as con:
        version_num = con.execute(text("SELECT version_num FROM alembic_version")).first()[0]
    return version_num or None
