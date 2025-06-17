"""Added force_confirmation_before_delete to ServerSettings

Revision ID: afcff5ebcf7c
Revises: d5a720d1b99b
Create Date: 2025-06-12 00:33:12.873850

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.alembic.alembic_utils import _table_has_column

# revision identifiers, used by Alembic.
revision = 'afcff5ebcf7c'
down_revision = 'd5a720d1b99b'
branch_labels = None
depends_on = None


def upgrade():
    if not _table_has_column(
        "server_settings",
        "force_confirmation_before_delete",
    ):
        op.add_column(
            "server_settings",
            sa.Column(
                "force_confirmation_before_delete",
                sa.Boolean,
                default=False,
            ),
        )
        op.execute(text("COMMIT"))


def downgrade():
    if _table_has_column(
        "server_settings",
        "force_confirmation_before_delete",
    ):
        op.drop_column("server_settings", "force_confirmation_before_delete")
