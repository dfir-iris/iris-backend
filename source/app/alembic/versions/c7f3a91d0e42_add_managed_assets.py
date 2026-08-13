"""Add the customer-bounded managed asset registry.

Creates `managed_asset` (the deduplicated per-customer inventory) and
`managed_asset_audit` (the append-only human change log), plus four
supporting indexes on pre-existing tables that the live sighting lookup
depends on.

The sighting lookup resolves a registry row to its `case_assets` rows on
`lower(asset_name) = normalized_name AND asset_type_id = ...`, so the
expression index on `case_assets` is load-bearing, not an optimisation.
`alert_assets_association` only had a composite PK led by `alert_id`,
which left asset -> alert lookups unindexed.

The upgrade ends with a one-shot backfill so an existing deployment gets
a populated registry immediately rather than filling in gradually as
new observations arrive, then grants the new `asset_manager_*` bits to
groups that already hold `server_administrator` — see
`_grant_admin_permissions` for why `post_init` cannot do this itself.

Idempotent via `_has_table` / `index_exists`; the backfill and the
permission grant are both naturally re-runnable (`ON CONFLICT DO NOTHING`
and a bitwise OR).

Revision ID: c7f3a91d0e42
Revises: b2e7f4a9c031
Create Date: 2026-08-12 10:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'c7f3a91d0e42'
down_revision = 'b2e7f4a9c031'
branch_labels = None
depends_on = None


# `regexp_replace(..., '\s+', ' ', 'g')` then `btrim` then `lower` is the
# SQL twin of the Python `' '.join(value.strip().split()).lower()` in
# `app/business/managed_assets.py`. The two MUST agree — a divergence
# would make the backfill produce rows the CHECK constraint rejects, or
# worse, rows that the application then fails to match.
_NORMALIZE_SQL = "lower(btrim(regexp_replace({column}, '\\s+', ' ', 'g')))"

# Pinned to what `ck_managed_asset_name_length` says at *this* revision.
# Deliberately a literal rather than an import of
# `MANAGED_ASSET_NAME_MAX_LENGTH`: a migration must keep describing the
# schema it created even after the model moves on.
_NAME_MAX_LENGTH = 512

# Permission bits from `models/authorization.py` at this revision, as
# literals for the same reason as `_NAME_MAX_LENGTH` — importing the enum
# would let a later renumbering silently rewrite what this migration did.
_PERM_SERVER_ADMINISTRATOR = 0x2
_PERM_ASSET_MANAGER_READ = 0x2000000
_PERM_ASSET_MANAGER_WRITE = 0x4000000


def _backfill():
    """Seed the registry from every existing case / alert asset.

    Two sources of customer attribution:
      * case-scoped assets -> `cases.client_id`
      * alert-only assets (`case_id IS NULL`) -> `alerts.alert_customer_id`

    `DISTINCT ON` collapses the many observations of one asset down to a
    single registry row, keeping an arbitrary-but-stable display name.
    Assets with no `asset_type_id` are skipped: the column is nullable on
    `case_assets` but is part of the registry identity, so there is no
    honest row to write.

    The two length rules are required, not cosmetic: `case_assets.asset_name`
    is an uncapped Text column and real deployments hold names longer than
    `ck_managed_asset_name_length` allows, so an unguarded backfill aborts
    the entire migration on the first one. A name over the limit only
    because of whitespace padding is cut to fit; `normalized_name` is never
    cut, since it is what resolves a registry row to its sightings, so an
    observation whose normalised name genuinely exceeds the limit is left
    out of the registry rather than stored under a mangled identity. The
    same two rules are applied by the observe statements in
    `datamgmt/manage/manage_managed_assets_db.py`, which is what keeps the
    backfill and everything after it consistent.
    """
    normalized = _NORMALIZE_SQL.format(column='ca.asset_name')
    op.execute(text(f"""
        INSERT INTO managed_asset (client_id, asset_type_id, name, normalized_name, source)
        SELECT DISTINCT ON (src.client_id, src.normalized_name, src.asset_type_id)
               src.client_id, src.asset_type_id,
               left(src.name, {_NAME_MAX_LENGTH}), src.normalized_name, 'observed'
        FROM (
            SELECT c.client_id          AS client_id,
                   ca.asset_type_id     AS asset_type_id,
                   ca.asset_name        AS name,
                   {normalized}         AS normalized_name
            FROM case_assets ca
            JOIN cases c ON c.case_id = ca.case_id
            WHERE ca.asset_name IS NOT NULL
              AND ca.asset_type_id IS NOT NULL
              AND c.client_id IS NOT NULL

            UNION ALL

            SELECT a.alert_customer_id  AS client_id,
                   ca.asset_type_id     AS asset_type_id,
                   ca.asset_name        AS name,
                   {normalized}         AS normalized_name
            FROM case_assets ca
            JOIN alert_assets_association aaa ON aaa.asset_id = ca.asset_id
            JOIN alerts a ON a.alert_id = aaa.alert_id
            WHERE ca.asset_name IS NOT NULL
              AND ca.asset_type_id IS NOT NULL
        ) AS src
        WHERE length(src.normalized_name) > 0
          AND length(src.normalized_name) <= {_NAME_MAX_LENGTH}
        ORDER BY src.client_id, src.normalized_name, src.asset_type_id, src.name
        ON CONFLICT DO NOTHING
    """))


def _grant_admin_permissions():
    """Give groups that already administer the server the registry bits.

    A fresh install needs nothing from this: `ac_get_mask_full_permissions()`
    derives the Administrators mask by walking the `Permissions` enum, so a
    newly-created group picks up `asset_manager_*` automatically. But
    `post_init` only seeds a group's mask on the boot that *creates* it —
    deliberately, so it stops stomping admin-edited permissions on every
    restart. The consequence is that a new permission bit never reaches a
    group that already exists, which would leave the registry invisible to
    exactly the people meant to administer it.

    Scoped to holders of `server_administrator`, so this hands nothing to
    anyone who was not already a full administrator. Read *and* write,
    matching what those groups would have been granted on a fresh install.
    """
    op.execute(text(f"""
        UPDATE groups
        SET group_permissions =
            group_permissions | {_PERM_ASSET_MANAGER_READ | _PERM_ASSET_MANAGER_WRITE}
        WHERE group_permissions & {_PERM_SERVER_ADMINISTRATOR} != 0
    """))


def upgrade():
    if not _has_table('managed_asset'):
        op.create_table(
            'managed_asset',
            sa.Column('managed_asset_id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('managed_asset_uuid', sa.UUID(), nullable=False,
                      server_default=sa.text('gen_random_uuid()')),
            sa.Column('client_id', sa.BigInteger(), nullable=False),
            sa.Column('asset_type_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('normalized_name', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('criticality', sa.String(length=16), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column('environment', sa.String(length=32), nullable=True),
            sa.Column('owner', sa.Text(), nullable=True),
            sa.Column('location', sa.Text(), nullable=True),
            sa.Column('tags', sa.Text(), nullable=True),
            sa.Column('ip', sa.Text(), nullable=True),
            sa.Column('domain', sa.Text(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('source', sa.String(length=16), nullable=False,
                      server_default=sa.text("'manual'")),
            sa.Column('custom_attributes', JSONB(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('created_by', sa.BigInteger(), nullable=True),
            sa.Column('updated_by', sa.BigInteger(), nullable=True),
            sa.ForeignKeyConstraint(['client_id'], ['client.client_id'], ondelete='CASCADE'),
            # RESTRICT, not CASCADE: silently deleting a customer's whole
            # inventory because an admin removed an asset *type* would be
            # data loss well beyond what that action implies.
            sa.ForeignKeyConstraint(['asset_type_id'], ['assets_type.asset_id'], ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['created_by'], ['user.id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['updated_by'], ['user.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('managed_asset_id'),
            sa.UniqueConstraint('managed_asset_uuid', name='uq_managed_asset_uuid'),
            sa.UniqueConstraint('client_id', 'normalized_name', 'asset_type_id',
                                name='uq_managed_asset_identity'),
            sa.CheckConstraint(
                'normalized_name = lower(btrim(normalized_name)) AND length(normalized_name) > 0',
                name='ck_managed_asset_normalized_name',
            ),
            sa.CheckConstraint('length(name) <= 512', name='ck_managed_asset_name_length'),
            sa.CheckConstraint(
                "criticality IN ('critical','high','medium','low','unknown')",
                name='ck_managed_asset_criticality',
            ),
            sa.CheckConstraint(
                "environment IS NULL OR environment IN "
                "('production','staging','development','test','dr','unknown')",
                name='ck_managed_asset_environment',
            ),
            sa.CheckConstraint(
                "source IN ('manual','observed','import')",
                name='ck_managed_asset_source',
            ),
        )
        op.create_index('idx_managed_asset_client', 'managed_asset', ['client_id'])
        op.create_index('idx_managed_asset_client_updated', 'managed_asset',
                        ['client_id', 'updated_at'])
        op.create_index('idx_managed_asset_client_criticality', 'managed_asset',
                        ['client_id', 'criticality'])
        # `text_pattern_ops` so `normalized_name LIKE 'srv-%'` can use the
        # index regardless of the database collation.
        op.execute(text(
            'CREATE INDEX idx_managed_asset_normalized_name_pattern '
            'ON managed_asset (normalized_name text_pattern_ops)'
        ))

    if not _has_table('managed_asset_audit'):
        op.create_table(
            'managed_asset_audit',
            sa.Column('audit_id', sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column('managed_asset_id', sa.BigInteger(), nullable=True),
            sa.Column('client_id', sa.BigInteger(), nullable=False),
            sa.Column('asset_name_snapshot', sa.Text(), nullable=False),
            sa.Column('asset_type_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(length=16), nullable=False),
            sa.Column('changes', JSONB(), nullable=True),
            sa.Column('user_id', sa.BigInteger(), nullable=True),
            sa.Column('user_login_snapshot', sa.Text(), nullable=True),
            sa.Column('source', sa.String(length=16), nullable=False,
                      server_default=sa.text("'api'")),
            sa.Column('occurred_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            # SET NULL everywhere: an audit entry that cascades away with
            # the thing it records is worthless. The `*_snapshot` columns
            # keep the entry readable after the referent is gone.
            sa.ForeignKeyConstraint(['managed_asset_id'], ['managed_asset.managed_asset_id'],
                                    ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['client_id'], ['client.client_id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['asset_type_id'], ['assets_type.asset_id'], ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('audit_id'),
            sa.CheckConstraint(
                "action IN ('create','update','delete','import')",
                name='ck_managed_asset_audit_action',
            ),
            sa.CheckConstraint(
                "source IN ('ui','api','import')",
                name='ck_managed_asset_audit_source',
            ),
        )
        op.create_index('idx_managed_asset_audit_asset', 'managed_asset_audit',
                        ['managed_asset_id', 'occurred_at'])
        op.create_index('idx_managed_asset_audit_client', 'managed_asset_audit',
                        ['client_id', 'occurred_at'])

    # Supporting indexes on existing tables — the sighting resolution
    # path scans these on every registry read.
    if _has_table('case_assets'):
        if not index_exists('case_assets', 'idx_case_assets_name_normalized_type'):
            # Indexes the *same* expression the sighting lookup compares
            # against — a plain `lower(asset_name)` index would be unusable
            # by that predicate and would also miss whitespace variants
            # ("SRV  DC01" vs "SRV DC01"). Every function here is IMMUTABLE,
            # which is what makes the expression indexable at all.
            op.execute(text(
                'CREATE INDEX idx_case_assets_name_normalized_type ON case_assets ('
                + _NORMALIZE_SQL.format(column='asset_name')
                + ', asset_type_id)'
            ))
        if not index_exists('case_assets', 'idx_case_assets_type_id'):
            op.create_index('idx_case_assets_type_id', 'case_assets', ['asset_type_id'])

    if _has_table('alert_assets_association'):
        if not index_exists('alert_assets_association', 'idx_alert_assets_assoc_asset_id'):
            op.create_index('idx_alert_assets_assoc_asset_id', 'alert_assets_association',
                            ['asset_id'])

    if _has_table('case_events_assets'):
        if not index_exists('case_events_assets', 'idx_case_events_assets_asset_id'):
            op.create_index('idx_case_events_assets_asset_id', 'case_events_assets', ['asset_id'])

    if _has_table('managed_asset'):
        _backfill()

    if _has_table('groups'):
        _grant_admin_permissions()


def downgrade():
    # Clear both bits everywhere rather than mirroring the scoped grant:
    # once the tables below are dropped the permission denotes a feature
    # that no longer exists, and a bit left behind on some group would
    # quietly re-enable the UI against a schema that cannot serve it.
    if _has_table('groups'):
        op.execute(text(
            'UPDATE groups SET group_permissions = group_permissions & ~'
            f'{_PERM_ASSET_MANAGER_READ | _PERM_ASSET_MANAGER_WRITE}'
        ))

    if _has_table('case_events_assets') and \
            index_exists('case_events_assets', 'idx_case_events_assets_asset_id'):
        op.drop_index('idx_case_events_assets_asset_id', table_name='case_events_assets')

    if _has_table('alert_assets_association') and \
            index_exists('alert_assets_association', 'idx_alert_assets_assoc_asset_id'):
        op.drop_index('idx_alert_assets_assoc_asset_id', table_name='alert_assets_association')

    if _has_table('case_assets'):
        if index_exists('case_assets', 'idx_case_assets_type_id'):
            op.drop_index('idx_case_assets_type_id', table_name='case_assets')
        if index_exists('case_assets', 'idx_case_assets_name_normalized_type'):
            op.drop_index('idx_case_assets_name_normalized_type', table_name='case_assets')

    if _has_table('managed_asset_audit'):
        op.drop_table('managed_asset_audit')

    if _has_table('managed_asset'):
        op.drop_table('managed_asset')
