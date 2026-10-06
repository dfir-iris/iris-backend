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

"""Persistence helpers for the war-room scope (assets / IOCs of the
attached cases, staged objects).

Every read helper takes the explicit list of case ids the caller is
allowed to see: the blueprint computes it (attached ∩ readable), this
layer never widens it.
"""

from sqlalchemy import func
from sqlalchemy import or_

from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_asset_counts_subquery
from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_asset_ids_with_vulnerability
from app.db import db
from app.models.assets import AnalysisStatus
from app.models.assets import AssetStage
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import Cases
from app.models.customers import Client
from app.models.iocs import Ioc
from app.models.iocs import Tlp
from app.models.models import IocAssetLink
from app.models.models import IocType
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomChatMessage
from app.models.war_rooms import WarRoomDecision
from app.models.war_rooms import WarRoomStagedObject


def _escape_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def war_room_scope_db_attached_case_ids(war_room_id):
    rows = (
        db.session.query(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .order_by(WarRoomCase.attached_at.asc(), WarRoomCase.case_id.asc())
        .all()
    )
    return [row.case_id for row in rows]


def war_room_scope_db_cases(case_ids):
    """`[(case_id, case_name, customer_id, customer_name)]` for the given ids."""
    if not case_ids:
        return []
    return (
        db.session.query(
            Cases.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
        )
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .filter(Cases.case_id.in_(list(case_ids)))
        .order_by(Cases.case_id.asc())
        .all()
    )


def _ioc_count_subquery():
    return (
        db.session.query(
            IocAssetLink.asset_id.label('asset_id'),
            func.count(IocAssetLink.ioc_asset_link_id).label('ioc_count'),
        )
        .group_by(IocAssetLink.asset_id)
        .subquery()
    )


def _filter_assets(query, vuln_sq, case_ids, search=None, stage_id=None, stage_none=False, compromised=False,
                   vulnerable=None, vulnerability=None):
    """Apply the scope asset filters to a query over `CaseAssets` already
    outer-joined to `vuln_sq`."""
    query = query.filter(CaseAssets.case_id.in_(list(case_ids)))
    if search:
        pattern = f'%{_escape_like(search)}%'
        query = query.filter(or_(
            CaseAssets.asset_name.ilike(pattern, escape='\\'),
            CaseAssets.asset_ip.ilike(pattern, escape='\\'),
            CaseAssets.asset_domain.ilike(pattern, escape='\\'),
            CaseAssets.asset_description.ilike(pattern, escape='\\'),
            CaseAssets.asset_tags.ilike(pattern, escape='\\'),
        ))
    if stage_none:
        query = query.filter(CaseAssets.stage_id.is_(None))
    elif stage_id is not None:
        query = query.filter(CaseAssets.stage_id == stage_id)
    if compromised:
        query = query.filter(CaseAssets.asset_compromise_status_id == 1)
    if vulnerable == 'open':
        query = query.filter(vuln_sq.c.vuln_open_count > 0)
    elif vulnerable == 'exploited':
        query = query.filter(vuln_sq.c.vuln_exploited_count > 0)
    elif vulnerable == 'none':
        query = query.filter(func.coalesce(vuln_sq.c.vuln_open_count, 0) == 0)
    if vulnerability:
        query = query.filter(CaseAssets.asset_id.in_(findings_db_asset_ids_with_vulnerability(vulnerability)))
    return query


def war_room_scope_db_assets(case_ids, search=None, stage_id=None, stage_none=False,
                             compromised=False, vulnerable=None, vulnerability=None, limit=5000,
                             offset=0, sort='name'):
    """Assets of `case_ids`, at most `limit + 1` rows from `offset` (caller
    detects truncation / a next page).

    `vulnerable` is None (no filter), `'open'` (an open finding),
    `'exploited'` (an exploited finding, fixed or not) or `'none'`
    (no open finding). `vulnerability` is a normalised identifier: only
    the assets with a finding (any status) on that entry or its aliases.
    `sort` is `'name'` (name, then id) or `'case'` (case, then name)."""
    if not case_ids:
        return []

    vuln_sq = findings_db_asset_counts_subquery()
    ioc_count_sq = _ioc_count_subquery()

    query = (
        db.session.query(
            CaseAssets.asset_id,
            CaseAssets.asset_uuid,
            CaseAssets.asset_name,
            CaseAssets.asset_type_id,
            AssetsType.asset_name.label('asset_type_name'),
            CaseAssets.asset_ip,
            CaseAssets.asset_domain,
            CaseAssets.asset_description,
            CaseAssets.asset_tags,
            CaseAssets.asset_compromise_status_id,
            CaseAssets.analysis_status_id,
            AnalysisStatus.name.label('analysis_status_name'),
            CaseAssets.stage_id,
            CaseAssets.stage_reason,
            CaseAssets.stage_decision_id,
            CaseAssets.stage_updated_at,
            CaseAssets.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
            func.coalesce(ioc_count_sq.c.ioc_count, 0).label('ioc_count'),
            func.coalesce(vuln_sq.c.vuln_open_count, 0).label('vuln_open_count'),
            func.coalesce(vuln_sq.c.vuln_total_count, 0).label('vuln_total_count'),
            func.coalesce(vuln_sq.c.vuln_exploited_count, 0).label('vuln_exploited_count'),
            func.coalesce(vuln_sq.c.vuln_exploited_open_count, 0).label('vuln_exploited_open_count'),
            vuln_sq.c.vuln_max_rank,
            CaseAssets.date_update,
        )
        .join(Cases, Cases.case_id == CaseAssets.case_id)
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .outerjoin(AssetsType, AssetsType.asset_id == CaseAssets.asset_type_id)
        .outerjoin(AnalysisStatus, AnalysisStatus.id == CaseAssets.analysis_status_id)
        .outerjoin(ioc_count_sq, ioc_count_sq.c.asset_id == CaseAssets.asset_id)
        .outerjoin(vuln_sq, vuln_sq.c.asset_id == CaseAssets.asset_id)
    )
    query = _filter_assets(query, vuln_sq, case_ids, search=search, stage_id=stage_id, stage_none=stage_none,
                           compromised=compromised, vulnerable=vulnerable, vulnerability=vulnerability)

    if sort == 'case':
        order = (CaseAssets.case_id.asc(), func.lower(CaseAssets.asset_name).asc(), CaseAssets.asset_id.asc())
    else:
        order = (func.lower(CaseAssets.asset_name).asc(), CaseAssets.asset_id.asc())
    query = query.order_by(*order)
    if offset:
        query = query.offset(offset)
    return query.limit(limit + 1).all()


def war_room_scope_db_assets_breakdown(case_ids, search=None, stage_id=None, stage_none=False,
                                       compromised=False, vulnerable=None, vulnerability=None):
    """Rows `(case_id, stage_id, stage_kind, assets, vuln_open, vuln_exploited_open)`
    over every asset matching the filters, grouped per case and stage.
    One query whatever the number of cases."""
    if not case_ids:
        return []
    vuln_sq = findings_db_asset_counts_subquery()
    query = (
        db.session.query(
            CaseAssets.case_id,
            CaseAssets.stage_id,
            AssetStage.kind.label('stage_kind'),
            func.count(CaseAssets.asset_id).label('assets'),
            func.coalesce(func.sum(vuln_sq.c.vuln_open_count), 0).label('vuln_open'),
            func.coalesce(func.sum(vuln_sq.c.vuln_exploited_open_count), 0).label('vuln_exploited_open'),
        )
        .outerjoin(AssetStage, AssetStage.id == CaseAssets.stage_id)
        .outerjoin(vuln_sq, vuln_sq.c.asset_id == CaseAssets.asset_id)
    )
    query = _filter_assets(query, vuln_sq, case_ids, search=search, stage_id=stage_id, stage_none=stage_none,
                           compromised=compromised, vulnerable=vulnerable, vulnerability=vulnerability)
    return query.group_by(CaseAssets.case_id, CaseAssets.stage_id, AssetStage.kind).all()


def war_room_scope_db_asset_sightings(case_ids, name_keys):
    """Distinct rows `(asset_type_id, name_key, case_id)` of the assets of
    `case_ids` whose lower-cased name is in `name_keys`. One query."""
    name_keys = list(name_keys or ())
    if not case_ids or not name_keys:
        return []
    name_key = func.lower(CaseAssets.asset_name)
    return (
        db.session.query(CaseAssets.asset_type_id, name_key.label('name_key'), CaseAssets.case_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)), name_key.in_(name_keys))
        .distinct()
        .all()
    )


def _ioc_key():
    return func.lower(func.trim(Ioc.ioc_value))


def _filter_iocs(query, case_ids, search=None):
    query = query.filter(Ioc.case_id.in_(list(case_ids)))
    if search:
        pattern = f'%{_escape_like(search)}%'
        query = query.filter(or_(
            Ioc.ioc_value.ilike(pattern, escape='\\'),
            Ioc.ioc_description.ilike(pattern, escape='\\'),
            Ioc.ioc_tags.ilike(pattern, escape='\\'),
        ))
    return query


def war_room_scope_db_iocs(case_ids, search=None, limit=5000, keys=None):
    """IOCs of `case_ids`, at most `limit + 1` rows (caller detects truncation).
    `keys` restricts to the given group keys (lower-cased trimmed values)."""
    if not case_ids:
        return []

    query = (
        db.session.query(
            Ioc.ioc_id,
            Ioc.ioc_value,
            Ioc.ioc_type_id,
            IocType.type_name.label('ioc_type_name'),
            Ioc.ioc_tlp_id,
            Tlp.tlp_name,
            Ioc.ioc_description,
            Ioc.ioc_tags,
            Ioc.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
        )
        .join(Cases, Cases.case_id == Ioc.case_id)
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .outerjoin(IocType, IocType.type_id == Ioc.ioc_type_id)
        .outerjoin(Tlp, Tlp.tlp_id == Ioc.ioc_tlp_id)
    )
    query = _filter_iocs(query, case_ids, search=search)
    if keys is not None:
        if not keys:
            return []
        query = query.filter(_ioc_key().in_(list(keys)))

    return (
        query
        .order_by(Ioc.ioc_value.asc(), Ioc.ioc_id.asc())
        .limit(limit + 1)
        .all()
    )


def war_room_scope_db_ioc_keys_page(case_ids, search=None, offset=0, limit=100, sort='value'):
    """One page of distinct IOC group keys over `case_ids`, as rows
    `(key, case_count)`. `sort` is `'value'` (alphabetical) or `'spread'`
    (seen in the most cases first)."""
    if not case_ids:
        return []
    key = _ioc_key()
    case_count = func.count(func.distinct(Ioc.case_id))
    query = _filter_iocs(db.session.query(key.label('key'), case_count.label('case_count')), case_ids, search=search)
    query = query.group_by(key)
    if sort == 'spread':
        query = query.order_by(case_count.desc(), key.asc())
    else:
        query = query.order_by(key.asc())
    return query.offset(offset).limit(limit).all()


def war_room_scope_db_ioc_keys_count(case_ids, search=None):
    """Number of distinct IOC group keys over `case_ids`."""
    if not case_ids:
        return 0
    query = _filter_iocs(db.session.query(func.count(func.distinct(_ioc_key()))), case_ids, search=search)
    return query.scalar() or 0


def war_room_scope_db_ioc_case_counts(case_ids, search=None):
    """Rows `(case_id, iocs)`: distinct IOC group keys per case."""
    if not case_ids:
        return []
    query = _filter_iocs(
        db.session.query(Ioc.case_id, func.count(func.distinct(_ioc_key())).label('iocs')),
        case_ids, search=search,
    )
    return query.group_by(Ioc.case_id).all()


def war_room_scope_db_assets_by_ids(asset_ids):
    if not asset_ids:
        return []
    return CaseAssets.query.filter(CaseAssets.asset_id.in_(list(asset_ids))).all()


def war_room_scope_db_iocs_by_ids(ioc_ids):
    if not ioc_ids:
        return []
    return Ioc.query.filter(Ioc.ioc_id.in_(list(ioc_ids))).all()


def war_room_scope_db_find_asset(case_id, asset_name, asset_type_id):
    """Id of the asset with the same (case-insensitive) name and type in `case_id`."""
    row = (
        db.session.query(CaseAssets.asset_id)
        .filter(
            CaseAssets.case_id == case_id,
            func.lower(CaseAssets.asset_name) == func.lower(asset_name),
            CaseAssets.asset_type_id == asset_type_id,
        )
        .order_by(CaseAssets.asset_id.asc())
        .first()
    )
    return row.asset_id if row else None


def war_room_scope_db_find_ioc(case_id, ioc_value):
    """Id of an IOC of `case_id` holding the same indicator: same value,
    trimmed and case-insensitive, whatever its type (mirrors
    `war_room_scope._ioc_group_key`)."""
    row = (
        db.session.query(Ioc.ioc_id)
        .filter(
            Ioc.case_id == case_id,
            func.lower(func.trim(Ioc.ioc_value)) == (ioc_value or '').strip().lower(),
        )
        .order_by(Ioc.ioc_id.asc())
        .first()
    )
    return row.ioc_id if row else None


def war_room_scope_db_asset_type_exists(asset_type_id):
    return db.session.query(AssetsType.asset_id).filter(AssetsType.asset_id == asset_type_id).first() is not None


def war_room_scope_db_analysis_status_exists(analysis_status_id):
    return db.session.query(AnalysisStatus.id).filter(AnalysisStatus.id == analysis_status_id).first() is not None


def war_room_scope_db_tlp_exists(tlp_id):
    return db.session.query(Tlp.tlp_id).filter(Tlp.tlp_id == tlp_id).first() is not None


def war_room_scope_db_stage_exists(stage_id):
    return db.session.query(AssetStage.id).filter(AssetStage.id == stage_id).first() is not None


def war_room_scope_db_ioc_type_get(ioc_type_id):
    return IocType.query.filter(IocType.type_id == ioc_type_id).first()


def war_room_scope_db_decision_in_room(war_room_id, decision_id):
    return db.session.query(WarRoomDecision.decision_id).filter(
        WarRoomDecision.war_room_id == war_room_id,
        WarRoomDecision.decision_id == decision_id,
    ).first() is not None


def war_room_scope_db_chat_message_in_room(war_room_id, message_id):
    return db.session.query(WarRoomChatMessage.message_id).filter(
        WarRoomChatMessage.war_room_id == war_room_id,
        WarRoomChatMessage.message_id == message_id,
    ).first() is not None


def war_room_scope_db_staged_list(war_room_id):
    return (
        db.session.query(WarRoomStagedObject, User.name.label('created_by_name'))
        .outerjoin(User, User.id == WarRoomStagedObject.created_by_id)
        .filter(WarRoomStagedObject.war_room_id == war_room_id)
        .order_by(WarRoomStagedObject.created_at.desc(), WarRoomStagedObject.id.desc())
        .all()
    )


def war_room_scope_db_staged_count(war_room_id):
    return (
        db.session.query(func.count(WarRoomStagedObject.id))
        .filter(WarRoomStagedObject.war_room_id == war_room_id)
        .scalar()
    ) or 0


def war_room_scope_db_staged_get(war_room_id, staged_id):
    return WarRoomStagedObject.query.filter(
        WarRoomStagedObject.war_room_id == war_room_id,
        WarRoomStagedObject.id == staged_id,
    ).first()


def war_room_scope_db_staged_add(staged):
    db.session.add(staged)
    db.session.commit()
    return staged


def war_room_scope_db_staged_save():
    db.session.commit()


def war_room_scope_db_staged_delete(staged):
    db.session.delete(staged)
    db.session.commit()


def war_room_scope_db_rollback():
    db.session.rollback()
