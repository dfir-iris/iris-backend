#  IRIS Source Code
#  Copyright (C) 2025 - DFIR-IRIS
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

import enum

from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import ForeignKey
from sqlalchemy import BigInteger
from sqlalchemy import UUID
from sqlalchemy import text
from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.db import db


class CompromiseStatus(enum.Enum):
    to_be_determined = 0x0
    compromised = 0x1
    not_compromised = 0x2
    unknown = 0x3

    @classmethod
    def has_value(cls, value):
        return value in cls._value2member_map_


class AssetsType(db.Model):
    __tablename__ = 'assets_type'

    asset_id = Column(Integer, primary_key=True)
    asset_name = Column(String(155))
    asset_description = Column(String(255))
    asset_icon_not_compromised = Column(String(255))
    asset_icon_compromised = Column(String(255))


alert_assets_association = Table(
    'alert_assets_association',
    db.Model.metadata,
    Column('alert_id', ForeignKey('alerts.alert_id'), primary_key=True),
    Column('asset_id', ForeignKey('case_assets.asset_id'), primary_key=True)
)


class CaseAssets(db.Model):
    __tablename__ = 'case_assets'

    asset_id = Column(BigInteger, primary_key=True)
    asset_uuid = Column(UUID(as_uuid=True), server_default=text("gen_random_uuid()"), nullable=False)
    asset_name = Column(Text)
    asset_description = Column(Text)
    asset_domain = Column(Text)
    asset_ip = Column(Text)
    asset_info = Column(Text)
    asset_compromise_status_id = Column(Integer, nullable=True)
    asset_type_id = Column(ForeignKey('assets_type.asset_id'))
    asset_tags = Column(Text)
    case_id = Column(ForeignKey('cases.case_id'))
    date_added = Column(DateTime)
    date_update = Column(DateTime)
    user_id = Column(ForeignKey('user.id'))
    analysis_status_id = Column(ForeignKey('analysis_status.id'))
    custom_attributes = Column(JSON)
    asset_enrichment = Column(JSONB)
    modification_history = Column(JSON)

    case = relationship('Cases')
    user = relationship('User', foreign_keys=[user_id])
    asset_type = relationship('AssetsType')
    analysis_status = relationship('AnalysisStatus')
    # Status flags (org-wide taxonomy, `AssetFlag`). Only writable through
    # the dedicated flag endpoints so every change lands in
    # `CaseAssetFlagHistory` and on the case "Asset status" timeline.
    flags = relationship('CaseAssetFlag', back_populates='asset', cascade='all, delete-orphan',
                         passive_deletes=True, lazy='selectin')

    alerts = relationship('Alert', secondary=alert_assets_association, back_populates='assets')
    iocs = relationship('IocAssetLink', back_populates='asset')


class AnalysisStatus(db.Model):
    __tablename__ = 'analysis_status'

    id = Column(Integer, primary_key=True)
    name = Column(Text)


ASSET_FLAG_KINDS = ('status', 'done', 'exception')

ASSET_FLAG_COLORS = (
    'slate', 'gray', 'red', 'orange', 'amber', 'yellow', 'lime', 'green',
    'emerald', 'teal', 'cyan', 'sky', 'blue', 'indigo', 'violet', 'purple',
    'fuchsia', 'pink', 'rose',
)


class AssetFlag(db.Model):
    """Org-wide taxonomy of asset status flags (Isolated, Patched, …).

    Flags are facts about an asset, in no particular order: an asset
    carries any combination of them. `kind` tells the UI and the
    war-room board how to aggregate a flag: `status` = a plain fact,
    `done` = the asset is back to normal, `exception` = accepted
    deviation that should be backed by a reason (and optionally a
    war-room decision).
    """
    __tablename__ = 'asset_flag'
    __table_args__ = (
        CheckConstraint("kind IN ('status', 'done', 'exception')", name='ck_asset_flag_kind'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    color = Column(String(16), nullable=False, server_default=text("'slate'"))
    icon = Column(String(64), nullable=True)
    kind = Column(String(16), nullable=False, server_default=text("'status'"))
    sort_order = Column(Integer, nullable=False, server_default=text('0'))
    requires_reason = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    requires_decision = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    created_at = Column(DateTime, nullable=False, server_default=text('now()'))


class CaseAssetFlag(db.Model):
    """A flag currently set on a case asset.

    `event_id` is the "Asset status" timeline event written when the flag
    was set; it is updated along with the flag while it still exists.
    """
    __tablename__ = 'case_asset_flag'

    asset_id = Column(BigInteger, ForeignKey('case_assets.asset_id', ondelete='CASCADE'), primary_key=True)
    flag_id = Column(Integer, ForeignKey('asset_flag.id'), primary_key=True, index=True)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    decision_id = Column(BigInteger, ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'),
                         nullable=True)
    event_id = Column(BigInteger, ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True)
    set_at = Column(DateTime, nullable=False, server_default=text('now()'))
    set_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    asset = relationship('CaseAssets', back_populates='flags')
    flag = relationship('AssetFlag', lazy='selectin')


class CaseAssetFlagHistory(db.Model):
    """Append-only log of the flag changes of a case asset.

    Flag names are denormalised so the history stays readable after an
    administrator renames or deletes a flag.
    """
    __tablename__ = 'case_asset_flag_history'
    __table_args__ = (
        CheckConstraint("action IN ('set', 'updated', 'cleared')", name='ck_case_asset_flag_history_action'),
    )

    id = Column(BigInteger, primary_key=True)
    asset_id = Column(BigInteger, ForeignKey('case_assets.asset_id', ondelete='CASCADE'),
                      nullable=False, index=True)
    case_id = Column(BigInteger, ForeignKey('cases.case_id', ondelete='CASCADE'),
                     nullable=False, index=True)
    flag_id = Column(Integer, ForeignKey('asset_flag.id', ondelete='SET NULL'), nullable=True)
    flag_name = Column(String(64), nullable=True)
    action = Column(String(16), nullable=False)
    reason = Column(Text, nullable=True)
    decision_id = Column(BigInteger, ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'),
                         nullable=True)
    war_room_id = Column(BigInteger, ForeignKey('war_room.war_room_id', ondelete='SET NULL'),
                         nullable=True)
    event_id = Column(BigInteger, ForeignKey('cases_events.event_id', ondelete='SET NULL'), nullable=True)
    changed_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    changed_at = Column(DateTime, nullable=False, server_default=text('now()'), index=True)

    changed_by = relationship('User')
