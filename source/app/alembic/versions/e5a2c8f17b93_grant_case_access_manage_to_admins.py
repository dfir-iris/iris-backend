#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Grant `case_access_manage` to the groups that already administer the server.

The permission is a bit on `Group.group_permissions`: no schema change, no
rows to seed. A fresh install needs nothing from this either —
`ac_get_mask_full_permissions()` walks the `Permissions` enum, so the
Administrators group is created with the bit. But `post_init` only seeds a
group's mask on the boot that creates it, so on an upgraded instance the
existing administrator groups would show the permission unticked where a
fresh install shows it ticked. Same gap, and same fix, as the
`asset_manager_*` bits in c7f3a91d0e42.

Scoped to holders of `server_administrator`, who can already change any
case's access through `/manage/...cases-access` and pass the new routes on
that permission alone: this hands nobody a right they did not have. Every
other group, Analysts included, is left alone — delegating the right is an
administrator's decision.

Idempotent: OR-ing a bit in twice is a no-op.

Revision ID: e5a2c8f17b93
Revises: c3f8a1d62e04
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

from app.alembic.alembic_utils import _has_table


revision = 'e5a2c8f17b93'
down_revision = 'c3f8a1d62e04'
branch_labels = None
depends_on = None

# Literal values, not `Permissions.*`: a migration must keep meaning what it
# meant when written, whatever the enum later becomes.
_PERM_SERVER_ADMINISTRATOR = 0x2
_PERM_CASE_ACCESS_MANAGE = 0x8000000


def upgrade():
    if not _has_table('groups'):
        return

    op.execute(text(f"""
        UPDATE groups
        SET group_permissions = group_permissions | {_PERM_CASE_ACCESS_MANAGE}
        WHERE group_permissions & {_PERM_SERVER_ADMINISTRATOR} != 0
    """))


def downgrade():
    # Clear the bit everywhere, not only on the groups granted above: below
    # this revision the permission names nothing, and the case-scoped routes
    # it gates do not exist.
    if not _has_table('groups'):
        return

    op.execute(text(
        f'UPDATE groups SET group_permissions = group_permissions & ~{_PERM_CASE_ACCESS_MANAGE}'
    ))
