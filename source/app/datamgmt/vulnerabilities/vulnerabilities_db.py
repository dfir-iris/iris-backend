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

"""Persistence of the vulnerability catalogue and of the findings.

Every helper reading findings takes the explicit list of case ids (and,
for the registry, customer ids) the caller may see; `None` means
"unrestricted" (server administrator) and an empty list means nothing.
This layer never widens what it is given.
"""

import datetime

from sqlalchemy import and_
from sqlalchemy import case as sa_case
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.datamgmt.manage.manage_managed_assets_db import managed_assets_db_scope_clauses
from app.db import db
from app.models.assets import AssetsType
from app.models.assets import CaseAssets
from app.models.authorization import User
from app.models.cases import Cases
from app.models.cases import CasesEvent
from app.models.customers import Client
from app.models.iocs import Ioc
from app.models.iocs import Tlp
from app.models.managed_assets import ManagedAsset
from app.models.models import IocType
from app.models.vulnerabilities import FINDING_DISMISSED_STATUSES
from app.models.vulnerabilities import FINDING_FIXED_STATUSES
from app.models.vulnerabilities import FINDING_OPEN_STATUSES
from app.models.vulnerabilities import VULNERABILITY_PRIVATE_PREFIX
from app.models.vulnerabilities import CaseAssetVulnerability
from app.models.vulnerabilities import CaseAssetVulnerabilityEvent
from app.models.vulnerabilities import CaseAssetVulnerabilityIoc
from app.models.vulnerabilities import ManagedAssetVulnerability
from app.models.vulnerabilities import Vulnerability
from app.models.vulnerabilities import VulnerabilityAlias
from app.models.vulnerabilities import VulnerabilityFindingHistory
from app.models.vulnerabilities import WarRoomVulnerability
from app.models.war_rooms import WarRoomDecision

_SEVERITY_RANKS = {'critical': 5, 'high': 4, 'medium': 3, 'low': 2, 'none': 1, 'unknown': 0}


def _escape_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def vulnerabilities_db_severity_rank():
    """SQL rank of `vulnerability.severity` (critical = 5 … unknown = 0)."""
    return sa_case(
        *[(Vulnerability.severity == name, rank) for name, rank in _SEVERITY_RANKS.items()],
        else_=0,
    )


def _restrict_cases(query, column, case_ids):
    if case_ids is not None:
        query = query.filter(column.in_(list(case_ids)))
    return query


# ---- Catalogue -------------------------------------------------------------

def vulnerabilities_db_get(vulnerability_id):
    return Vulnerability.query.filter(Vulnerability.vulnerability_id == vulnerability_id).first()


def vulnerabilities_db_get_many(vulnerability_ids):
    """{vulnerability_id: vulnerability} of `vulnerability_ids`."""
    if not vulnerability_ids:
        return {}
    rows = Vulnerability.query.filter(Vulnerability.vulnerability_id.in_(list(vulnerability_ids))).all()
    return {row.vulnerability_id: row for row in rows}


def vulnerabilities_db_find(identifier):
    """Entry whose identifier or one of whose aliases is `identifier` (normalised)."""
    row = Vulnerability.query.filter(Vulnerability.identifier == identifier).first()
    if row is not None:
        return row
    return (
        Vulnerability.query
        .join(VulnerabilityAlias, VulnerabilityAlias.vulnerability_id == Vulnerability.vulnerability_id)
        .filter(VulnerabilityAlias.alias == identifier)
        .first()
    )


def vulnerabilities_db_find_many(identifiers):
    """{identifier or alias: entry} for the given normalised identifiers."""
    identifiers = list(identifiers or ())
    if not identifiers:
        return {}
    found = {row.identifier: row
             for row in Vulnerability.query.filter(Vulnerability.identifier.in_(identifiers)).all()}
    rows = (
        db.session.query(VulnerabilityAlias.alias, Vulnerability)
        .join(Vulnerability, Vulnerability.vulnerability_id == VulnerabilityAlias.vulnerability_id)
        .filter(VulnerabilityAlias.alias.in_(identifiers))
        .all()
    )
    for alias, row in rows:
        found.setdefault(alias, row)
    return found


def vulnerabilities_db_identifier_owner(identifier):
    """Id of the entry holding `identifier` as identifier or alias, else None."""
    row = vulnerabilities_db_find(identifier)
    return row.vulnerability_id if row is not None else None


def vulnerabilities_db_aliases(vulnerability_ids):
    """{vulnerability_id: [alias, …]} (sorted)."""
    result = {vulnerability_id: [] for vulnerability_id in vulnerability_ids or ()}
    if not result:
        return result
    rows = (
        db.session.query(VulnerabilityAlias.vulnerability_id, VulnerabilityAlias.alias)
        .filter(VulnerabilityAlias.vulnerability_id.in_(list(result)))
        .order_by(VulnerabilityAlias.alias.asc())
        .all()
    )
    for vulnerability_id, alias in rows:
        result[vulnerability_id].append(alias)
    return result


def vulnerabilities_db_replace_aliases(vulnerability_id, aliases):
    """Replace the alias set of an entry (no commit)."""
    VulnerabilityAlias.query.filter(VulnerabilityAlias.vulnerability_id == vulnerability_id).delete(
        synchronize_session=False)
    for alias in aliases:
        db.session.add(VulnerabilityAlias(vulnerability_id=vulnerability_id, alias=alias))


def vulnerabilities_db_last_private_number(year):
    """Highest `NNNN` allocated as `IRIS-VULN-<year>-NNNN` (0 if none),
    looking at identifiers and aliases (a merged private entry keeps its
    identifier as an alias, so its number is never handed out again)."""
    prefix = f'{VULNERABILITY_PRIVATE_PREFIX}{year}-'
    pattern = f'{_escape_like(prefix)}%'
    highest = 0
    for column in (Vulnerability.identifier, VulnerabilityAlias.alias):
        for (value,) in db.session.query(column).filter(column.like(pattern, escape='\\')).all():
            suffix = value[len(prefix):]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return highest


def vulnerabilities_db_tlp_exists(tlp_id):
    return db.session.query(Tlp.tlp_id).filter(Tlp.tlp_id == tlp_id).first() is not None


def vulnerabilities_db_user_names(user_ids):
    ids = [user_id for user_id in set(user_ids or ()) if user_id is not None]
    if not ids:
        return {}
    return {row.id: row.name for row in db.session.query(User.id, User.name).filter(User.id.in_(ids)).all()}


def vulnerabilities_db_user_exists(user_id):
    return db.session.query(User.id).filter(User.id == user_id).first() is not None


def _catalogue_filters(query, filters, case_ids):
    search = filters.get('search')
    if search:
        pattern = f'%{_escape_like(search)}%'
        alias_match = (
            select(VulnerabilityAlias.alias_id)
            .where(VulnerabilityAlias.vulnerability_id == Vulnerability.vulnerability_id,
                   VulnerabilityAlias.alias.ilike(pattern, escape='\\'))
            .exists()
        )
        content_match = or_(
            Vulnerability.title.ilike(pattern, escape='\\'),
            Vulnerability.tags.ilike(pattern, escape='\\'),
            alias_match,
        )
        if filters.get('private_content_hidden'):
            # Matching on content the caller is not shown would leak it.
            content_match = and_(Vulnerability.is_private.is_(False), content_match)
        query = query.filter(or_(
            Vulnerability.identifier.ilike(pattern, escape='\\'),
            content_match,
        ))
    if filters.get('severities'):
        query = query.filter(Vulnerability.severity.in_(filters['severities']))
    if filters.get('kinds'):
        query = query.filter(Vulnerability.kind.in_(filters['kinds']))
    if filters.get('kev') is not None:
        query = query.filter(Vulnerability.kev.is_(filters['kev']))
    if filters.get('is_private') is not None:
        query = query.filter(Vulnerability.is_private.is_(filters['is_private']))
    if filters.get('affected'):
        # Entries with an open finding in a case the caller can read.
        affected = (
            select(CaseAssetVulnerability.finding_id)
            .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
            .where(CaseAssetVulnerability.vulnerability_id == Vulnerability.vulnerability_id,
                   CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES))
        )
        if case_ids is not None:
            affected = affected.where(CaseAssets.case_id.in_(list(case_ids)))
        query = query.filter(affected.exists())
    return query


_CATALOGUE_SORTS = {
    'identifier': Vulnerability.identifier,
    'title': Vulnerability.title,
    'cvss_score': Vulnerability.cvss_score,
    'epss_score': Vulnerability.epss_score,
    'published_at': Vulnerability.published_at,
    'created_at': Vulnerability.created_at,
    'updated_at': Vulnerability.updated_at,
}


def vulnerabilities_db_search(filters, page, per_page, order_by, direction, case_ids):
    """`(entries, total)` of one catalogue page. `order_by` is one of
    `_CATALOGUE_SORTS` or `severity` (validated by the caller)."""
    query = _catalogue_filters(Vulnerability.query, filters, case_ids)
    total = query.order_by(None).count()

    column = vulnerabilities_db_severity_rank() if order_by == 'severity' else _CATALOGUE_SORTS[order_by]
    ordering = column.desc() if direction == 'desc' else column.asc()
    rows = (
        query
        .order_by(ordering.nulls_last(), Vulnerability.vulnerability_id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return rows, total


def vulnerabilities_db_sortable_fields():
    return tuple(_CATALOGUE_SORTS) + ('severity',)


def vulnerabilities_db_count_findings(vulnerability_id):
    """Total number of findings (every case and registry asset, unscoped)."""
    case_count = db.session.query(func.count(CaseAssetVulnerability.finding_id)).filter(
        CaseAssetVulnerability.vulnerability_id == vulnerability_id).scalar() or 0
    registry_count = db.session.query(func.count(ManagedAssetVulnerability.finding_id)).filter(
        ManagedAssetVulnerability.vulnerability_id == vulnerability_id).scalar() or 0
    return case_count + registry_count


def _status_counts(status_column, exploitation_column):
    return (
        func.count().label('findings'),
        func.count().filter(status_column.in_(FINDING_OPEN_STATUSES)).label('open'),
        func.count().filter(status_column.in_(FINDING_FIXED_STATUSES)).label('fixed'),
        func.count().filter(status_column.in_(FINDING_DISMISSED_STATUSES)).label('dismissed'),
        func.count().filter(exploitation_column == 'exploited').label('exploited'),
    )


def vulnerabilities_db_case_finding_counts(vulnerability_ids, case_ids):
    """{vulnerability_id: row(findings, open, fixed, dismissed, exploited, cases)} over `case_ids`."""
    if not vulnerability_ids:
        return {}
    query = (
        db.session.query(
            CaseAssetVulnerability.vulnerability_id,
            *_status_counts(CaseAssetVulnerability.remediation_status, CaseAssetVulnerability.exploitation_status),
            func.count(func.distinct(CaseAssets.case_id)).label('cases'),
        )
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssetVulnerability.vulnerability_id.in_(list(vulnerability_ids)),
                CaseAssets.case_id.isnot(None))
    )
    query = _restrict_cases(query, CaseAssets.case_id, case_ids)
    return {row.vulnerability_id: row for row in query.group_by(CaseAssetVulnerability.vulnerability_id).all()}


def vulnerabilities_db_registry_finding_counts(vulnerability_ids, registry_scope):
    """Same as above for the registry findings `registry_scope` may see."""
    if not vulnerability_ids:
        return {}
    query = (
        db.session.query(
            ManagedAssetVulnerability.vulnerability_id,
            *_status_counts(ManagedAssetVulnerability.remediation_status,
                            ManagedAssetVulnerability.exploitation_status),
        )
        .join(ManagedAsset, ManagedAsset.managed_asset_id == ManagedAssetVulnerability.managed_asset_id)
        .filter(ManagedAssetVulnerability.vulnerability_id.in_(list(vulnerability_ids)),
                *managed_assets_db_scope_clauses(registry_scope))
    )
    return {row.vulnerability_id: row for row in query.group_by(ManagedAssetVulnerability.vulnerability_id).all()}


def vulnerabilities_db_exposure_cases(vulnerability_id, case_ids):
    """Per-case counts of the findings of an entry, over `case_ids`."""
    query = (
        db.session.query(
            CaseAssets.case_id,
            Cases.name.label('case_name'),
            Cases.client_id.label('customer_id'),
            Client.name.label('customer_name'),
            Cases.close_date,
            *_status_counts(CaseAssetVulnerability.remediation_status, CaseAssetVulnerability.exploitation_status),
        )
        .select_from(CaseAssetVulnerability)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .join(Cases, Cases.case_id == CaseAssets.case_id)
        .outerjoin(Client, Client.client_id == Cases.client_id)
        .filter(CaseAssetVulnerability.vulnerability_id == vulnerability_id)
    )
    query = _restrict_cases(query, CaseAssets.case_id, case_ids)
    return (
        query
        .group_by(CaseAssets.case_id, Cases.name, Cases.client_id, Client.name, Cases.close_date)
        .order_by(CaseAssets.case_id.desc())
        .all()
    )


def vulnerabilities_db_exposure_registry(vulnerability_id, registry_scope):
    """Registry findings of an entry that `registry_scope` may see."""
    query = (
        db.session.query(
            ManagedAssetVulnerability,
            ManagedAsset.name.label('asset_name'),
            ManagedAsset.client_id,
            Client.name.label('customer_name'),
            ManagedAsset.criticality,
        )
        .join(ManagedAsset, ManagedAsset.managed_asset_id == ManagedAssetVulnerability.managed_asset_id)
        .outerjoin(Client, Client.client_id == ManagedAsset.client_id)
        .filter(ManagedAssetVulnerability.vulnerability_id == vulnerability_id,
                *managed_assets_db_scope_clauses(registry_scope))
    )
    return query.order_by(ManagedAsset.name.asc()).limit(2000).all()


def vulnerabilities_db_merge(source_id, target_id):
    """Move every finding (and war-room tracking) of `source_id` onto
    `target_id`, then delete the source (no commit). A finding the target already has on the same
    asset wins: the source one is dropped. Returns `(moved, dropped)`."""
    moved = 0
    dropped = 0
    for model, asset_column in ((CaseAssetVulnerability, CaseAssetVulnerability.asset_id),
                                (ManagedAssetVulnerability, ManagedAssetVulnerability.managed_asset_id)):
        target_assets = select(asset_column).where(model.vulnerability_id == target_id)
        duplicate_query = model.query.filter(model.vulnerability_id == source_id, asset_column.in_(target_assets))
        dropped += duplicate_query.count()
        duplicate_query.delete(synchronize_session=False)
        moved += model.query.filter(model.vulnerability_id == source_id).update(
            {model.vulnerability_id: target_id}, synchronize_session=False)
    # War-room tracking follows too (not counted: it is not a finding).
    tracking_rooms = select(WarRoomVulnerability.war_room_id).where(WarRoomVulnerability.vulnerability_id == target_id)
    WarRoomVulnerability.query.filter(WarRoomVulnerability.vulnerability_id == source_id,
                                      WarRoomVulnerability.war_room_id.in_(tracking_rooms)
                                      ).delete(synchronize_session=False)
    WarRoomVulnerability.query.filter(WarRoomVulnerability.vulnerability_id == source_id).update(
        {WarRoomVulnerability.vulnerability_id: target_id}, synchronize_session=False)
    Vulnerability.query.filter(Vulnerability.vulnerability_id == source_id).delete(synchronize_session=False)
    return moved, dropped


def vulnerabilities_db_add(row):
    db.session.add(row)


def vulnerabilities_db_flush() -> bool:
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def vulnerabilities_db_commit() -> bool:
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def vulnerabilities_db_rollback():
    db.session.rollback()


def vulnerabilities_db_delete(row) -> bool:
    try:
        db.session.delete(row)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


# ---- Case findings ---------------------------------------------------------

def _case_findings_query():
    owner = aliased(User)
    return (
        db.session.query(
            CaseAssetVulnerability,
            Vulnerability,
            CaseAssets.asset_name,
            CaseAssets.case_id,
            CaseAssets.asset_type_id,
            AssetsType.asset_name.label('asset_type_name'),
            CaseAssets.asset_compromise_status_id,
            Cases.name.label('case_name'),
            owner.name.label('owner_name'),
        )
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .join(Cases, Cases.case_id == CaseAssets.case_id)
        .outerjoin(AssetsType, AssetsType.asset_id == CaseAssets.asset_type_id)
        .outerjoin(owner, owner.id == CaseAssetVulnerability.owner_id)
    )


def _finding_filters(query, model, filters, today):
    if filters.get('remediation_statuses'):
        query = query.filter(model.remediation_status.in_(filters['remediation_statuses']))
    group = filters.get('status_group')
    if group == 'open':
        query = query.filter(model.remediation_status.in_(FINDING_OPEN_STATUSES))
    elif group == 'fixed':
        query = query.filter(model.remediation_status.in_(FINDING_FIXED_STATUSES))
    elif group == 'dismissed':
        query = query.filter(model.remediation_status.in_(FINDING_DISMISSED_STATUSES))
    if filters.get('exploitation_statuses'):
        query = query.filter(model.exploitation_status.in_(filters['exploitation_statuses']))
    if filters.get('severities'):
        query = query.filter(Vulnerability.severity.in_(filters['severities']))
    if filters.get('kev') is not None:
        query = query.filter(Vulnerability.kev.is_(filters['kev']))
    if filters.get('vulnerability_id') is not None:
        query = query.filter(model.vulnerability_id == filters['vulnerability_id'])
    if filters.get('overdue'):
        query = query.filter(model.due_date < today, model.remediation_status.in_(FINDING_OPEN_STATUSES))
    return query


def findings_db_case_list(case_ids, filters, limit):
    """Findings of `case_ids`, at most `limit + 1` rows (caller detects truncation)."""
    if case_ids is not None and not case_ids:
        return []
    query = _restrict_cases(_case_findings_query(), CaseAssets.case_id, case_ids)
    query = _finding_filters(query, CaseAssetVulnerability, filters, datetime.date.today())
    if filters.get('asset_id') is not None:
        query = query.filter(CaseAssetVulnerability.asset_id == filters['asset_id'])
    search = filters.get('search')
    if search:
        pattern = f'%{_escape_like(search)}%'
        query = query.filter(or_(
            Vulnerability.identifier.ilike(pattern, escape='\\'),
            Vulnerability.title.ilike(pattern, escape='\\'),
            CaseAssets.asset_name.ilike(pattern, escape='\\'),
            CaseAssetVulnerability.component.ilike(pattern, escape='\\'),
        ))
    return (
        query
        .order_by(vulnerabilities_db_severity_rank().desc(), Vulnerability.identifier.asc(),
                  func.lower(CaseAssets.asset_name).asc(), CaseAssetVulnerability.finding_id.asc())
        .limit(limit + 1)
        .all()
    )


def findings_db_case_get(finding_id):
    """Row of `_case_findings_query` for one finding, or None."""
    return _case_findings_query().filter(CaseAssetVulnerability.finding_id == finding_id).first()


def findings_db_case_for_asset(asset_id):
    """Rows of `_case_findings_query` for every finding of one asset."""
    return (
        _case_findings_query()
        .filter(CaseAssetVulnerability.asset_id == asset_id)
        .order_by(CaseAssetVulnerability.finding_id.asc())
        .all()
    )


def findings_db_case_for_case(case_id):
    """Rows of `_case_findings_query` for every finding of a case."""
    return (
        _case_findings_query()
        .filter(CaseAssets.case_id == case_id)
        .order_by(CaseAssetVulnerability.finding_id.asc())
        .all()
    )


def findings_db_case_existing(asset_ids, vulnerability_id):
    """Asset ids among `asset_ids` already carrying a finding for the entry."""
    if not asset_ids:
        return set()
    rows = (
        db.session.query(CaseAssetVulnerability.asset_id)
        .filter(CaseAssetVulnerability.asset_id.in_(list(asset_ids)),
                CaseAssetVulnerability.vulnerability_id == vulnerability_id)
        .all()
    )
    return {row.asset_id for row in rows}


def findings_db_case_assets(case_id, asset_ids):
    """{asset_id: asset} for the ids of `asset_ids` belonging to `case_id`."""
    if not asset_ids:
        return {}
    rows = CaseAssets.query.filter(CaseAssets.case_id == case_id, CaseAssets.asset_id.in_(list(asset_ids))).all()
    return {row.asset_id: row for row in rows}


def findings_db_case_event_ids(case_id, event_ids):
    if not event_ids:
        return set()
    rows = db.session.query(CasesEvent.event_id).filter(
        CasesEvent.case_id == case_id, CasesEvent.event_id.in_(list(event_ids))).all()
    return {row.event_id for row in rows}


def findings_db_case_ioc_ids(case_id, ioc_ids):
    if not ioc_ids:
        return set()
    rows = db.session.query(Ioc.ioc_id).filter(Ioc.case_id == case_id, Ioc.ioc_id.in_(list(ioc_ids))).all()
    return {row.ioc_id for row in rows}


def findings_db_case_evidence(finding_ids):
    """`({finding_id: [(event_id, title, date)]}, {finding_id: [(ioc_id, value, type)]})`."""
    events = {finding_id: [] for finding_id in finding_ids or ()}
    iocs = {finding_id: [] for finding_id in finding_ids or ()}
    if not events:
        return events, iocs
    for row in (
        db.session.query(CaseAssetVulnerabilityEvent.finding_id, CasesEvent.event_id, CasesEvent.event_title,
                         CasesEvent.event_date)
        .join(CasesEvent, CasesEvent.event_id == CaseAssetVulnerabilityEvent.event_id)
        .filter(CaseAssetVulnerabilityEvent.finding_id.in_(list(events)))
        .order_by(CasesEvent.event_date.asc())
        .all()
    ):
        events[row.finding_id].append(row)
    for row in (
        db.session.query(CaseAssetVulnerabilityIoc.finding_id, Ioc.ioc_id, Ioc.ioc_value,
                         IocType.type_name.label('ioc_type_name'))
        .join(Ioc, Ioc.ioc_id == CaseAssetVulnerabilityIoc.ioc_id)
        .outerjoin(IocType, IocType.type_id == Ioc.ioc_type_id)
        .filter(CaseAssetVulnerabilityIoc.finding_id.in_(list(iocs)))
        .order_by(Ioc.ioc_value.asc())
        .all()
    ):
        iocs[row.finding_id].append(row)
    return events, iocs


def findings_db_case_set_events(finding_id, event_ids):
    CaseAssetVulnerabilityEvent.query.filter(CaseAssetVulnerabilityEvent.finding_id == finding_id).delete(
        synchronize_session=False)
    for event_id in event_ids:
        db.session.add(CaseAssetVulnerabilityEvent(finding_id=finding_id, event_id=event_id))


def findings_db_case_set_iocs(finding_id, ioc_ids):
    CaseAssetVulnerabilityIoc.query.filter(CaseAssetVulnerabilityIoc.finding_id == finding_id).delete(
        synchronize_session=False)
    for ioc_id in ioc_ids:
        db.session.add(CaseAssetVulnerabilityIoc(finding_id=finding_id, ioc_id=ioc_id))


def findings_db_delete_for_case(case_id):
    """Delete the findings of every asset of a case (no commit). Used by
    case deletion, before the assets an alert still references are
    detached from the case."""
    asset_ids = select(CaseAssets.asset_id).where(CaseAssets.case_id == case_id)
    CaseAssetVulnerability.query.filter(CaseAssetVulnerability.asset_id.in_(asset_ids)).delete(
        synchronize_session=False)


# ---- Registry findings -----------------------------------------------------

def _managed_findings_query():
    owner = aliased(User)
    return (
        db.session.query(ManagedAssetVulnerability, Vulnerability, owner.name.label('owner_name'))
        .join(Vulnerability, Vulnerability.vulnerability_id == ManagedAssetVulnerability.vulnerability_id)
        .outerjoin(owner, owner.id == ManagedAssetVulnerability.owner_id)
    )


def findings_db_managed_list(managed_asset_id, filters):
    query = _managed_findings_query().filter(ManagedAssetVulnerability.managed_asset_id == managed_asset_id)
    query = _finding_filters(query, ManagedAssetVulnerability, filters, datetime.date.today())
    return (
        query
        .order_by(vulnerabilities_db_severity_rank().desc(), Vulnerability.identifier.asc())
        .all()
    )


def findings_db_managed_get(managed_asset_id, finding_id):
    return _managed_findings_query().filter(
        ManagedAssetVulnerability.managed_asset_id == managed_asset_id,
        ManagedAssetVulnerability.finding_id == finding_id,
    ).first()


def findings_db_managed_exists(managed_asset_id, vulnerability_id):
    return db.session.query(ManagedAssetVulnerability.finding_id).filter(
        ManagedAssetVulnerability.managed_asset_id == managed_asset_id,
        ManagedAssetVulnerability.vulnerability_id == vulnerability_id,
    ).first() is not None


def findings_db_managed_from_cases(case_asset_ids_stmt, limit=1000):
    """Case findings on the case assets selected by `case_asset_ids_stmt`
    (the visible sightings of a registry asset)."""
    return (
        _case_findings_query()
        .filter(CaseAssetVulnerability.asset_id.in_(case_asset_ids_stmt))
        .order_by(vulnerabilities_db_severity_rank().desc(), CaseAssets.case_id.desc())
        .limit(limit)
        .all()
    )


# ---- Shared ----------------------------------------------------------------

def findings_db_add(row):
    db.session.add(row)


def findings_db_flush() -> bool:
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def findings_db_commit() -> bool:
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def findings_db_rollback():
    db.session.rollback()


def findings_db_delete(row) -> bool:
    try:
        db.session.delete(row)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def findings_db_add_history(entry):
    db.session.add(entry)


def findings_db_history(case_finding_id=None, managed_finding_id=None):
    """History rows of one finding, newest first, with user / decision labels."""
    query = (
        db.session.query(
            VulnerabilityFindingHistory,
            User.name.label('changed_by_name'),
            WarRoomDecision.number.label('decision_number'),
            WarRoomDecision.war_room_id.label('decision_war_room_id'),
        )
        .outerjoin(User, User.id == VulnerabilityFindingHistory.changed_by_id)
        .outerjoin(WarRoomDecision, WarRoomDecision.decision_id == VulnerabilityFindingHistory.decision_id)
    )
    if case_finding_id is not None:
        query = query.filter(VulnerabilityFindingHistory.case_finding_id == case_finding_id)
    else:
        query = query.filter(VulnerabilityFindingHistory.managed_finding_id == managed_finding_id)
    return query.order_by(VulnerabilityFindingHistory.changed_at.desc(),
                          VulnerabilityFindingHistory.history_id.desc()).all()


# ---- War room --------------------------------------------------------------

def findings_db_matrix(case_ids, vulnerability_ids=None):
    """Rows `(vulnerability, case_id, findings, open, fixed, dismissed, exploited)`
    grouped per entry and case, over `case_ids` (and `vulnerability_ids`
    when given)."""
    if not case_ids:
        return []
    query = (
        db.session.query(
            Vulnerability,
            CaseAssets.case_id,
            *_status_counts(CaseAssetVulnerability.remediation_status, CaseAssetVulnerability.exploitation_status),
        )
        .select_from(CaseAssetVulnerability)
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)))
    )
    if vulnerability_ids is not None:
        query = query.filter(Vulnerability.vulnerability_id.in_(list(vulnerability_ids)))
    return query.group_by(Vulnerability.vulnerability_id, CaseAssets.case_id).all()


def _matrix_entry_totals(case_ids):
    """Per entry: findings, open and exploited counts over `case_ids`."""
    return (
        select(
            CaseAssetVulnerability.vulnerability_id.label('vulnerability_id'),
            func.count().label('findings'),
            func.count().filter(
                CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES)).label('open'),
            func.count().filter(CaseAssetVulnerability.exploitation_status == 'exploited').label('exploited'),
        )
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .where(CaseAssets.case_id.in_(list(case_ids)))
        .group_by(CaseAssetVulnerability.vulnerability_id)
        .subquery()
    )


def _matrix_search(query, filters):
    search = filters.get('search')
    if search:
        pattern = f'%{_escape_like(search)}%'
        title_match = Vulnerability.title.ilike(pattern, escape='\\')
        if filters.get('private_content_hidden'):
            title_match = and_(Vulnerability.is_private.is_(False), title_match)
        query = query.filter(or_(Vulnerability.identifier.ilike(pattern, escape='\\'), title_match))
    return query


def findings_db_matrix_page(case_ids, war_room_id, filters, page, per_page):
    """`(vulnerability_ids, total)` of one matrix page: the entries with a
    finding in `case_ids` plus those `war_room_id` tracks, worst first:
    exploited and still open, then severity, open findings, identifier.
    `filters`: `search` (identifier or title), `tracked_only` (only the
    tracked entries), `observed_only` (only those with a finding)."""
    tracked = select(WarRoomVulnerability.vulnerability_id).where(WarRoomVulnerability.war_room_id == war_room_id)
    totals = _matrix_entry_totals(case_ids or [-1])
    query = (
        db.session.query(Vulnerability.vulnerability_id)
        .outerjoin(totals, totals.c.vulnerability_id == Vulnerability.vulnerability_id)
    )
    observed = totals.c.vulnerability_id.isnot(None)
    if filters.get('tracked_only'):
        query = query.filter(Vulnerability.vulnerability_id.in_(tracked))
        if filters.get('observed_only'):
            query = query.filter(observed)
    elif filters.get('observed_only'):
        query = query.filter(observed)
    else:
        query = query.filter(or_(observed, Vulnerability.vulnerability_id.in_(tracked)))
    query = _matrix_search(query, filters)
    total = query.order_by(None).count()

    open_count = func.coalesce(totals.c.open, 0)
    exploited_open = sa_case((and_(open_count > 0, func.coalesce(totals.c.exploited, 0) > 0), 0), else_=1)
    rows = (
        query
        .order_by(exploited_open, vulnerabilities_db_severity_rank().desc(), open_count.desc(),
                  Vulnerability.identifier)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return [row.vulnerability_id for row in rows], total


def findings_db_matrix_case_totals(case_ids, war_room_id, filters):
    """Rows `(case_id, findings, open, fixed, dismissed, exploited)`: the
    per-case column totals over every entry the matrix filters keep."""
    if not case_ids:
        return []
    query = (
        db.session.query(
            CaseAssets.case_id,
            *_status_counts(CaseAssetVulnerability.remediation_status, CaseAssetVulnerability.exploitation_status),
        )
        .select_from(CaseAssetVulnerability)
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)))
    )
    if filters.get('tracked_only'):
        query = query.filter(Vulnerability.vulnerability_id.in_(
            select(WarRoomVulnerability.vulnerability_id).where(WarRoomVulnerability.war_room_id == war_room_id)))
    query = _matrix_search(query, filters)
    return query.group_by(CaseAssets.case_id).all()


def tracked_db_counts(war_room_id, case_ids):
    """`(tracked, tracked_unobserved)` of `war_room_id`: the entries it
    tracks, and those without any finding in `case_ids`."""
    observed = (
        select(CaseAssetVulnerability.finding_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .where(CaseAssetVulnerability.vulnerability_id == WarRoomVulnerability.vulnerability_id,
               CaseAssets.case_id.in_(list(case_ids or [-1])))
        .exists()
    )
    row = (
        db.session.query(func.count().label('tracked'), func.count().filter(~observed).label('unobserved'))
        .select_from(WarRoomVulnerability)
        .filter(WarRoomVulnerability.war_room_id == war_room_id)
        .one()
    )
    return row.tracked, row.unobserved


def findings_db_asset_counts_subquery():
    """Per case-asset counts: open findings, exploited findings (any
    remediation), exploited-and-open findings, highest open severity rank."""
    is_open = CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES)
    is_exploited = CaseAssetVulnerability.exploitation_status == 'exploited'
    return (
        db.session.query(
            CaseAssetVulnerability.asset_id.label('asset_id'),
            func.count().filter(is_open).label('vuln_open_count'),
            func.count().label('vuln_total_count'),
            func.count().filter(is_exploited).label('vuln_exploited_count'),
            func.count().filter(and_(is_open, is_exploited)).label('vuln_exploited_open_count'),
            func.max(sa_case((is_open, vulnerabilities_db_severity_rank()), else_=None)).label('vuln_max_rank'),
        )
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .group_by(CaseAssetVulnerability.asset_id)
        .subquery()
    )


def findings_db_asset_tags(asset_ids):
    """Rows `(asset_id, finding_id, vulnerability_id, identifier, severity,
    kev, remediation_status, exploitation_status)` of the findings recorded
    on the given case assets, highest severity first."""
    asset_ids = list(asset_ids or ())
    if not asset_ids:
        return []
    return (
        db.session.query(
            CaseAssetVulnerability.asset_id,
            CaseAssetVulnerability.finding_id,
            Vulnerability.vulnerability_id,
            Vulnerability.identifier,
            Vulnerability.severity,
            Vulnerability.kev,
            CaseAssetVulnerability.remediation_status,
            CaseAssetVulnerability.exploitation_status,
        )
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .filter(CaseAssetVulnerability.asset_id.in_(asset_ids))
        .order_by(vulnerabilities_db_severity_rank().desc(), Vulnerability.identifier.asc())
        .all()
    )


def findings_db_asset_ids_with_vulnerability(identifier):
    """Select of the case-asset ids carrying a finding on the entry whose
    identifier or one of whose aliases is `identifier` (normalised)."""
    alias_owner = select(VulnerabilityAlias.vulnerability_id).where(VulnerabilityAlias.alias == identifier)
    return (
        select(CaseAssetVulnerability.asset_id)
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .where(or_(Vulnerability.identifier == identifier, Vulnerability.vulnerability_id.in_(alias_owner)))
    )


def findings_db_board_totals(case_ids, today):
    """One row of totals over the findings of `case_ids`."""
    is_open = CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES)
    is_exploited = CaseAssetVulnerability.exploitation_status == 'exploited'
    return (
        db.session.query(
            func.count().label('findings'),
            func.count().filter(is_open).label('open'),
            func.count().filter(CaseAssetVulnerability.remediation_status.in_(FINDING_FIXED_STATUSES)).label('fixed'),
            func.count().filter(
                CaseAssetVulnerability.remediation_status.in_(FINDING_DISMISSED_STATUSES)).label('dismissed'),
            func.count().filter(and_(is_open, is_exploited)).label('exploited_open'),
            func.count().filter(and_(is_open, CaseAssetVulnerability.due_date < today)).label('overdue'),
            func.count(func.distinct(CaseAssetVulnerability.asset_id)).filter(is_open).label('assets_open'),
            func.count(func.distinct(CaseAssetVulnerability.vulnerability_id)).label('vulnerabilities'),
            func.count().filter(and_(is_open, Vulnerability.kev.is_(True))).label('kev_open'),
        )
        .select_from(CaseAssetVulnerability)
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)))
        .one()
    )


def findings_db_exploited_open(case_ids, limit):
    """Exploited findings still open, worst first."""
    return (
        db.session.query(
            CaseAssetVulnerability.finding_id,
            CaseAssetVulnerability.asset_id,
            CaseAssets.asset_name,
            CaseAssets.case_id,
            Vulnerability.identifier,
            Vulnerability.severity,
        )
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)),
                CaseAssetVulnerability.exploitation_status == 'exploited',
                CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES))
        .order_by(vulnerabilities_db_severity_rank().desc(), CaseAssets.case_id.asc(),
                  CaseAssetVulnerability.finding_id.asc())
        .limit(limit)
        .all()
    )


def findings_db_overdue(case_ids, today, limit):
    """Open findings past their due date, oldest due first."""
    return (
        db.session.query(
            CaseAssetVulnerability.finding_id,
            CaseAssetVulnerability.asset_id,
            CaseAssetVulnerability.due_date,
            CaseAssets.asset_name,
            CaseAssets.case_id,
            Vulnerability.identifier,
        )
        .join(Vulnerability, Vulnerability.vulnerability_id == CaseAssetVulnerability.vulnerability_id)
        .join(CaseAssets, CaseAssets.asset_id == CaseAssetVulnerability.asset_id)
        .filter(CaseAssets.case_id.in_(list(case_ids)),
                CaseAssetVulnerability.remediation_status.in_(FINDING_OPEN_STATUSES),
                CaseAssetVulnerability.due_date < today)
        .order_by(CaseAssetVulnerability.due_date.asc(), CaseAssetVulnerability.finding_id.asc())
        .limit(limit)
        .all()
    )


def tracked_db_list(war_room_id, vulnerability_ids=None):
    """Rows `(tracking, vulnerability, added_by_name)` of the entries
    tracked by `war_room_id` (among `vulnerability_ids` when given),
    oldest first."""
    query = (
        db.session.query(WarRoomVulnerability, Vulnerability, User.name)
        .join(Vulnerability, Vulnerability.vulnerability_id == WarRoomVulnerability.vulnerability_id)
        .outerjoin(User, User.id == WarRoomVulnerability.added_by_id)
        .filter(WarRoomVulnerability.war_room_id == war_room_id)
    )
    if vulnerability_ids is not None:
        query = query.filter(WarRoomVulnerability.vulnerability_id.in_(list(vulnerability_ids)))
    return query.order_by(WarRoomVulnerability.added_at, WarRoomVulnerability.vulnerability_id).all()


def tracked_db_get(war_room_id, vulnerability_id):
    return WarRoomVulnerability.query.filter(
        WarRoomVulnerability.war_room_id == war_room_id,
        WarRoomVulnerability.vulnerability_id == vulnerability_id,
    ).first()


def tracked_db_add(tracking):
    db.session.add(tracking)


def tracked_db_delete(tracking):
    db.session.delete(tracking)
