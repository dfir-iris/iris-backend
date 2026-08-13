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

"""Customer-bounded asset registry (Manage -> Assets).

A `ManagedAsset` row is the durable, deduplicated record of a single
asset for a single customer. `case_assets` rows remain what they always
were — a per-case (or per-alert) observation — and the registry sits
above them holding the facts that outlive any one investigation:
criticality, owner, environment, custom attributes.

Identity is `(client_id, normalized_name, asset_type_id)`. `SRV-DC01`
and `srv-dc01` are two `case_assets` rows but one managed asset, which
is the entire point of an inventory. See `managed_assets_normalize_name`
in the business layer for the single source of truth on normalisation.

There is deliberately no link table between a registry row and the
`case_assets` rows it covers. Several existing write paths mutate or
bulk-delete `case_assets` without firing ORM events (case deletion
nulls `case_id` in bulk, alert deletion bulk-deletes, escalation flips
an alert-only asset's `case_id` in place), so a materialised link would
silently drift. Sightings are resolved live on
`lower(asset_name) = normalized_name AND asset_type_id = ...`.

Nor are there stored `first_seen_at` / `last_seen_at` / `compromised_at`
columns: a stored last-seen maintained on every observation would leak
the *timing* of a case the reader cannot open. Every derived timestamp
and count is computed per-caller over the sightings that caller may
actually see.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UUID
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import db


MANAGED_ASSET_CRITICALITIES = ('critical', 'high', 'medium', 'low', 'unknown')
MANAGED_ASSET_ENVIRONMENTS = ('production', 'staging', 'development', 'test', 'dr', 'unknown')
# How the registry row came to exist. `observed` rows were created by an
# ingest hook or by reconcile; `manual` and `import` are human-driven.
MANAGED_ASSET_SOURCES = ('manual', 'observed', 'import')

# Longest name the registry can hold, enforced by the CHECK below, by the
# marshmallow validator and by the observe paths. `case_assets.asset_name`
# is an uncapped Text column, so an observation can legitimately carry a
# longer name than the registry accepts; the observe paths cut the display
# name to fit and drop observations whose *normalised* name is longer than
# this, since such a name cannot be stored as an identity at all.
MANAGED_ASSET_NAME_MAX_LENGTH = 512

MANAGED_ASSET_AUDIT_ACTIONS = ('create', 'update', 'delete', 'import')
MANAGED_ASSET_AUDIT_SOURCES = ('ui', 'api', 'import')


def _sql_in_list(values):
    """Render one of the tuples above as a SQL literal list.

    The CHECK constraints and the marshmallow validators enforce the same
    enumerations, so they are built from one definition rather than
    written out twice and left to drift. The values are module constants,
    never request data.
    """
    return ','.join(f"'{value}'" for value in values)


# Columns a caller may sort the registry by. Everything here is a real
# scalar column on `managed_asset`.
#
# This allowlist is not a nicety: `paginate()` in `datamgmt/filtering.py`
# gates ordering on `hasattr(model, order_by)`, which is also true for
# `query`, `metadata`, `registry` and every relationship — so an
# unvalidated `?order_by=query` reaches `order_by(<Query object>)` and
# 500s. Sorting is done here rather than through that helper.
#
# Deliberately absent: anything derived from sightings. Ordering by
# "last seen" would rank assets by the timing of cases the caller may
# not be able to open, which is an ordering oracle over exactly the
# information the scope filter exists to hide.
MANAGED_ASSET_SORTABLE_FIELDS = frozenset({
    'name',
    'criticality',
    'environment',
    'owner',
    'location',
    'ip',
    'domain',
    'is_active',
    'source',
    'created_at',
    'updated_at',
    'managed_asset_id',
})


class ManagedAsset(db.Model):
    __tablename__ = 'managed_asset'

    managed_asset_id = Column(BigInteger, primary_key=True)
    managed_asset_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'),
                                nullable=False)

    client_id = Column(ForeignKey('client.client_id', ondelete='CASCADE'), nullable=False)
    asset_type_id = Column(ForeignKey('assets_type.asset_id', ondelete='RESTRICT'), nullable=False)

    # `name` is what the analyst typed and what we render.
    # `normalized_name` is the dedup key and is never user-visible.
    name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False)

    description = Column(Text)
    criticality = Column(String(16), nullable=False, default='unknown',
                         server_default=text("'unknown'"))
    environment = Column(String(32))
    owner = Column(Text)
    location = Column(Text)
    # Comma-separated, mirroring `CaseAssets.asset_tags` so the two
    # surfaces stay copy-pasteable.
    tags = Column(Text)
    ip = Column(Text)
    domain = Column(Text)

    is_active = Column(Boolean, nullable=False, default=True, server_default=text('true'))
    source = Column(String(16), nullable=False, default='manual', server_default=text("'manual'"))

    custom_attributes = Column(JSONB)

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    created_by = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_by = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    client = relationship('Client')
    asset_type = relationship('AssetsType')

    __table_args__ = (
        UniqueConstraint('managed_asset_uuid', name='uq_managed_asset_uuid'),
        UniqueConstraint('client_id', 'normalized_name', 'asset_type_id',
                         name='uq_managed_asset_identity'),
        # Defence in depth against a normalisation bypass: the schema
        # sets `normalized_name`, but a direct SQL insert that skipped it
        # would break the UNIQUE's dedup guarantee silently. Rejecting
        # anything not already normalised keeps the invariant honest.
        CheckConstraint(
            'normalized_name = lower(btrim(normalized_name)) AND length(normalized_name) > 0',
            name='ck_managed_asset_normalized_name',
        ),
        CheckConstraint(f'length(name) <= {MANAGED_ASSET_NAME_MAX_LENGTH}',
                        name='ck_managed_asset_name_length'),
        CheckConstraint(
            f'criticality IN ({_sql_in_list(MANAGED_ASSET_CRITICALITIES)})',
            name='ck_managed_asset_criticality',
        ),
        CheckConstraint(
            f'environment IS NULL OR environment IN ({_sql_in_list(MANAGED_ASSET_ENVIRONMENTS)})',
            name='ck_managed_asset_environment',
        ),
        CheckConstraint(
            f'source IN ({_sql_in_list(MANAGED_ASSET_SOURCES)})',
            name='ck_managed_asset_source',
        ),
        Index('idx_managed_asset_client', 'client_id'),
        Index('idx_managed_asset_client_updated', 'client_id', 'updated_at'),
        Index('idx_managed_asset_client_criticality', 'client_id', 'criticality'),
    )


class ManagedAssetAudit(db.Model):
    """Append-only record of every human change to a registry row.

    `observe` and `reconcile` deliberately do NOT write here — they are
    the highest-volume writers in the feature and would bury the human
    trail under machine noise. They emit a single `track_activity` row
    per batch instead, and the `action` CHECK below makes it awkward to
    change that by accident.

    Both foreign keys are `SET NULL`, not `CASCADE`: a delete record
    that vanishes when the asset is deleted records nothing. The
    `*_snapshot` columns carry the human-readable identity forward so
    the entry still reads correctly once the asset (or the acting user)
    is gone.
    """
    __tablename__ = 'managed_asset_audit'

    audit_id = Column(BigInteger, primary_key=True)
    managed_asset_id = Column(ForeignKey('managed_asset.managed_asset_id', ondelete='SET NULL'),
                              nullable=True)
    # Denormalised so the audit list endpoint can be access-filtered
    # with one index probe instead of a join back to a possibly-deleted
    # asset row.
    client_id = Column(ForeignKey('client.client_id', ondelete='CASCADE'), nullable=False)
    asset_name_snapshot = Column(Text, nullable=False)
    asset_type_id = Column(ForeignKey('assets_type.asset_id', ondelete='SET NULL'), nullable=True)

    action = Column(String(16), nullable=False)
    # {"criticality": {"from": "low", "to": "critical"}}
    changes = Column(JSONB)

    user_id = Column(ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    user_login_snapshot = Column(Text)
    source = Column(String(16), nullable=False, default='api', server_default=text("'api'"))
    occurred_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            f'action IN ({_sql_in_list(MANAGED_ASSET_AUDIT_ACTIONS)})',
            name='ck_managed_asset_audit_action',
        ),
        CheckConstraint(
            f'source IN ({_sql_in_list(MANAGED_ASSET_AUDIT_SOURCES)})',
            name='ck_managed_asset_audit_source',
        ),
        Index('idx_managed_asset_audit_asset', 'managed_asset_id', 'occurred_at'),
        Index('idx_managed_asset_audit_client', 'client_id', 'occurred_at'),
    )
