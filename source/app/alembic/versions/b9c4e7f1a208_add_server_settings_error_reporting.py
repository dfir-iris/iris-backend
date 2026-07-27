"""Error reporting — extend server_settings with six toggle/DSN columns.

Off by default on every install. Backend DSN column holds Fernet
ciphertext (same wrap as `mail_smtp_password`); the frontend DSN is
plaintext because the SPA has to read it at boot to init the Sentry
SDK — DSNs are ingest tokens, not secrets. See
`app/iris_engine/observability/reporter.py` (added in a follow-up
revision) for the consumers.

Idempotent via `_has_table` / `_table_has_column`.

Revision ID: b9c4e7f1a208
Revises: f7a3b9c1d02e
Create Date: 2026-07-27 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import _table_has_column


revision = 'b9c4e7f1a208'
down_revision = 'f7a3b9c1d02e'
branch_labels = None
depends_on = None


_ERROR_REPORTING_COLUMNS = [
    ('error_reporting_enabled', sa.Boolean(), sa.text('false')),
    # Fernet-encrypted at rest via SECRET_KEY — never plaintext.
    ('error_reporting_backend_dsn', sa.Text(), None),
    # Plaintext by design: exposed via `/api/v2/runtime-config` so the
    # browser can init the Sentry SDK.
    ('error_reporting_frontend_dsn', sa.Text(), None),
    ('error_reporting_environment', sa.String(length=64), None),
    ('error_reporting_sample_rate', sa.Numeric(precision=3, scale=2),
     sa.text('1.00')),
    ('error_reporting_include_user', sa.Boolean(), sa.text('false')),
]


def upgrade():
    if not _has_table('server_settings'):
        return
    for name, type_, default in _ERROR_REPORTING_COLUMNS:
        if _table_has_column('server_settings', name):
            continue
        column = (
            sa.Column(name, type_, nullable=True, server_default=default)
            if default is not None
            else sa.Column(name, type_, nullable=True)
        )
        op.add_column('server_settings', column)


def downgrade():
    if not _has_table('server_settings'):
        return
    for name, _, __ in _ERROR_REPORTING_COLUMNS:
        if _table_has_column('server_settings', name):
            op.drop_column('server_settings', name)
