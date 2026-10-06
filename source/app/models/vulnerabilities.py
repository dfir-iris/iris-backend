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

"""Vulnerability tracking.

Two layers:

- `Vulnerability` is the instance-wide catalogue: one row per weakness,
  public (`CVE-2024-3400`, `GHSA-…`, a vendor advisory id) or private
  to this IRIS instance (`IRIS-VULN-2026-0001`: a misconfiguration,
  weak credentials, a zero-day found during an investigation). Private
  entries are internal to IRIS: never sent to an external feed and
  never exported by a case transfer.
- A *finding* is "this asset is (or was) affected by this
  vulnerability". `CaseAssetVulnerability` hangs off a case asset (and
  therefore a case, and every war room the case is attached to);
  `ManagedAssetVulnerability` hangs off a registry asset, for exposure
  known outside of any investigation.

A finding tracks two independent axes: remediation (is the asset still
vulnerable?) and exploitation (was it used against the asset?). An
asset can be patched *and* exploited — the exploitation is a historic
fact the patch does not erase.

Findings are deliberately not linked to their case through a stored
`case_id`: `case_assets.case_id` is rewritten in bulk by several paths
(case deletion, alert escalation), so the case is always reached
through the asset.
"""

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import Float
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UUID
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declared_attr

from app.db import db


VULNERABILITY_KINDS = (
    'cve', 'advisory', 'misconfiguration', 'weak-credentials', 'exposed-service',
    'design-flaw', 'zero-day', 'other',
)
VULNERABILITY_SEVERITIES = ('critical', 'high', 'medium', 'low', 'none', 'unknown')
VULNERABILITY_EXPLOIT_MATURITIES = ('unknown', 'none', 'poc', 'weaponized', 'in-the-wild')
VULNERABILITY_PATCH_AVAILABILITIES = ('unknown', 'patch', 'workaround', 'none')
VULNERABILITY_SOURCES = ('manual', 'module', 'import')
VULNERABILITY_CVSS_VERSIONS = ('2.0', '3.0', '3.1', '4.0')

# Prefix of the identifiers allocated to private entries. Reserved: a
# public entry can never carry it.
VULNERABILITY_PRIVATE_PREFIX = 'IRIS-VULN-'
VULNERABILITY_IDENTIFIER_MAX_LENGTH = 64

# Remediation of a finding. `open` = the asset is (or may be) still
# vulnerable, `fixed` = remediated, `dismissed` = closed without a fix.
FINDING_OPEN_STATUSES = ('under-analysis', 'affected', 'mitigated')
FINDING_FIXED_STATUSES = ('patched', 'verified')
FINDING_DISMISSED_STATUSES = ('not-affected', 'risk-accepted', 'false-positive')
FINDING_REMEDIATION_STATUSES = FINDING_OPEN_STATUSES + FINDING_FIXED_STATUSES + FINDING_DISMISSED_STATUSES

FINDING_EXPLOITATION_STATUSES = ('unknown', 'not-exploited', 'attempted', 'suspected', 'exploited')

# CISA VEX justifications for `not-affected`.
FINDING_NOT_AFFECTED_JUSTIFICATIONS = (
    'component_not_present',
    'vulnerable_code_not_present',
    'vulnerable_code_not_in_execute_path',
    'vulnerable_code_cannot_be_controlled_by_adversary',
    'inline_mitigations_already_exist',
)

FINDING_HISTORY_ACTIONS = ('create', 'update')


def _sql_in_list(values):
    """Render one of the module tuples above as a SQL literal list (module
    constants only, never request data)."""
    return ','.join(f"'{value}'" for value in values)


class Vulnerability(db.Model):
    __tablename__ = 'vulnerability'

    vulnerability_id = Column(BigInteger, primary_key=True)
    vulnerability_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'),
                                nullable=False)

    # Normalised (`CVE-2024-3400`, `GHSA-xxxx-xxxx-xxxx`, `IRIS-VULN-2026-0001`)
    identifier = Column(String(VULNERABILITY_IDENTIFIER_MAX_LENGTH), nullable=False)
    is_private = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    kind = Column(String(32), nullable=False, default='other', server_default=text("'other'"))

    title = Column(Text, nullable=False)
    description = Column(Text)

    cvss_version = Column(String(8))
    cvss_vector = Column(Text)
    cvss_score = Column(Float)
    severity = Column(String(16), nullable=False, default='unknown', server_default=text("'unknown'"))

    epss_score = Column(Float)
    epss_percentile = Column(Float)
    epss_date = Column(Date)

    # CISA Known Exploited Vulnerabilities
    kev = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    kev_date_added = Column(Date)
    kev_due_date = Column(Date)
    kev_ransomware = Column(Boolean, nullable=False, default=False, server_default=text('false'))

    exploit_maturity = Column(String(16), nullable=False, default='unknown',
                              server_default=text("'unknown'"))
    patch_availability = Column(String(16), nullable=False, default='unknown',
                                server_default=text("'unknown'"))

    # ["CWE-79", …]
    cwes = Column(JSONB)
    # [{"vendor": …, "product": …, "versions": …}, …]
    affected_products = Column(JSONB)
    # ["https://…", …]
    reference_urls = Column(JSONB)

    published_at = Column(Date)
    modified_at = Column(Date)

    tlp_id = Column(Integer, ForeignKey('tlp.tlp_id', ondelete='SET NULL'), nullable=True)
    # Comma-separated, like the other IRIS objects.
    tags = Column(Text)
    source = Column(String(16), nullable=False, default='manual', server_default=text("'manual'"))
    # Free-form data written by enrichment modules.
    enrichment = Column(JSONB)

    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))
    created_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    updated_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    __table_args__ = (
        UniqueConstraint('identifier', name='uq_vulnerability_identifier'),
        UniqueConstraint('vulnerability_uuid', name='uq_vulnerability_uuid'),
        CheckConstraint(f'kind IN ({_sql_in_list(VULNERABILITY_KINDS)})', name='ck_vulnerability_kind'),
        CheckConstraint(f'severity IN ({_sql_in_list(VULNERABILITY_SEVERITIES)})',
                        name='ck_vulnerability_severity'),
        CheckConstraint(f'exploit_maturity IN ({_sql_in_list(VULNERABILITY_EXPLOIT_MATURITIES)})',
                        name='ck_vulnerability_exploit_maturity'),
        CheckConstraint(f'patch_availability IN ({_sql_in_list(VULNERABILITY_PATCH_AVAILABILITIES)})',
                        name='ck_vulnerability_patch_availability'),
        CheckConstraint(f'source IN ({_sql_in_list(VULNERABILITY_SOURCES)})', name='ck_vulnerability_source'),
        CheckConstraint(
            f'cvss_version IS NULL OR cvss_version IN ({_sql_in_list(VULNERABILITY_CVSS_VERSIONS)})',
            name='ck_vulnerability_cvss_version',
        ),
        CheckConstraint('cvss_score IS NULL OR (cvss_score >= 0 AND cvss_score <= 10)',
                        name='ck_vulnerability_cvss_score'),
        CheckConstraint('epss_score IS NULL OR (epss_score >= 0 AND epss_score <= 1)',
                        name='ck_vulnerability_epss_score'),
        CheckConstraint('epss_percentile IS NULL OR (epss_percentile >= 0 AND epss_percentile <= 1)',
                        name='ck_vulnerability_epss_percentile'),
        # The private prefix is reserved to private entries, and private
        # entries always carry it.
        CheckConstraint(
            f"is_private = (identifier LIKE '{VULNERABILITY_PRIVATE_PREFIX}%')",
            name='ck_vulnerability_private_identifier',
        ),
        Index('idx_vulnerability_severity', 'severity'),
        Index('idx_vulnerability_updated', 'updated_at'),
    )


class VulnerabilityAlias(db.Model):
    """Other identifiers of a catalogue entry (a GHSA of a CVE, the
    identifier of an entry merged into this one…). Unique across the
    catalogue, so a lookup by any of them lands on one entry."""
    __tablename__ = 'vulnerability_alias'

    alias_id = Column(BigInteger, primary_key=True)
    vulnerability_id = Column(BigInteger, ForeignKey('vulnerability.vulnerability_id', ondelete='CASCADE'),
                              nullable=False, index=True)
    alias = Column(String(VULNERABILITY_IDENTIFIER_MAX_LENGTH), nullable=False)

    __table_args__ = (
        UniqueConstraint('alias', name='uq_vulnerability_alias'),
    )


def _finding_checks(prefix):
    return (
        CheckConstraint(f'remediation_status IN ({_sql_in_list(FINDING_REMEDIATION_STATUSES)})',
                        name=f'ck_{prefix}_remediation_status'),
        CheckConstraint(f'exploitation_status IN ({_sql_in_list(FINDING_EXPLOITATION_STATUSES)})',
                        name=f'ck_{prefix}_exploitation_status'),
        CheckConstraint(
            'not_affected_justification IS NULL OR '
            f'not_affected_justification IN ({_sql_in_list(FINDING_NOT_AFFECTED_JUSTIFICATIONS)})',
            name=f'ck_{prefix}_justification',
        ),
        # An accepted risk is a decision someone has to be able to explain.
        CheckConstraint(
            "remediation_status <> 'risk-accepted' OR length(btrim(coalesce(status_reason, ''))) > 0",
            name=f'ck_{prefix}_risk_reason',
        ),
    )


class _FindingMixin:
    """Columns shared by case and registry findings."""

    remediation_status = Column(String(24), nullable=False, default='under-analysis',
                                server_default=text("'under-analysis'"))
    not_affected_justification = Column(String(64))
    # Why the status is what it is (required for risk-accepted)
    status_reason = Column(Text)

    exploitation_status = Column(String(16), nullable=False, default='unknown',
                                 server_default=text("'unknown'"))
    exploited_at = Column(DateTime)

    # Scanner, EDR, advisory, manual review, …
    detection_source = Column(String(128))
    detected_at = Column(DateTime)

    component = Column(Text)
    installed_version = Column(Text)
    fixed_version = Column(Text)
    notes = Column(Text)

    due_date = Column(Date)
    verified_at = Column(DateTime)
    verification_method = Column(Text)

    created_at = Column(DateTime, nullable=False, server_default=text('now()'))
    updated_at = Column(DateTime, nullable=False, server_default=text('now()'))

    @declared_attr
    def vulnerability_id(cls):
        # RESTRICT: a catalogue entry in use is merged, never dropped.
        return Column(BigInteger, ForeignKey('vulnerability.vulnerability_id', ondelete='RESTRICT'),
                      nullable=False, index=True)

    @declared_attr
    def owner_id(cls):
        return Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    @declared_attr
    def verified_by_id(cls):
        return Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    @declared_attr
    def created_by_id(cls):
        return Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    @declared_attr
    def updated_by_id(cls):
        return Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


class CaseAssetVulnerability(_FindingMixin, db.Model):
    """A case asset affected by a catalogue vulnerability."""
    __tablename__ = 'case_asset_vulnerability'

    finding_id = Column(BigInteger, primary_key=True)
    finding_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'), nullable=False)
    asset_id = Column(BigInteger, ForeignKey('case_assets.asset_id', ondelete='CASCADE'), nullable=False)
    # The war-room decision backing the current status (risk acceptance,
    # emergency patch window, …).
    decision_id = Column(BigInteger, ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'),
                         nullable=True)

    __table_args__ = (
        UniqueConstraint('asset_id', 'vulnerability_id', name='uq_case_asset_vulnerability'),
        *_finding_checks('case_asset_vuln'),
        Index('idx_case_asset_vuln_asset', 'asset_id'),
        Index('idx_case_asset_vuln_status', 'remediation_status'),
    )


class ManagedAssetVulnerability(_FindingMixin, db.Model):
    """A registry asset affected by a catalogue vulnerability, outside of
    any case (vulnerability-management feed, audit, …)."""
    __tablename__ = 'managed_asset_vulnerability'

    finding_id = Column(BigInteger, primary_key=True)
    finding_uuid = Column(UUID(as_uuid=True), server_default=text('gen_random_uuid()'), nullable=False)
    managed_asset_id = Column(BigInteger, ForeignKey('managed_asset.managed_asset_id', ondelete='CASCADE'),
                              nullable=False)

    __table_args__ = (
        UniqueConstraint('managed_asset_id', 'vulnerability_id', name='uq_managed_asset_vulnerability'),
        *_finding_checks('managed_asset_vuln'),
        Index('idx_managed_asset_vuln_asset', 'managed_asset_id'),
    )


class WarRoomVulnerability(db.Model):
    """A catalogue vulnerability tracked by a war room, whether or not any
    asset of its cases is known to be affected yet (a crisis opened on a
    fresh CVE, before the exposure is assessed)."""
    __tablename__ = 'war_room_vulnerability'

    war_room_id = Column(BigInteger, ForeignKey('war_room.war_room_id', ondelete='CASCADE'), primary_key=True)
    vulnerability_id = Column(BigInteger, ForeignKey('vulnerability.vulnerability_id', ondelete='CASCADE'),
                              primary_key=True)
    note = Column(Text)
    added_at = Column(DateTime, nullable=False, server_default=text('now()'))
    added_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)

    __table_args__ = (
        Index('idx_war_room_vulnerability_vulnerability', 'vulnerability_id'),
    )


class VulnerabilityFindingHistory(db.Model):
    """Append-only log of the changes of a finding (case or registry —
    exactly one of the two references is set)."""
    __tablename__ = 'vulnerability_finding_history'

    history_id = Column(BigInteger, primary_key=True)
    case_finding_id = Column(BigInteger,
                             ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'),
                             nullable=True)
    managed_finding_id = Column(BigInteger,
                                ForeignKey('managed_asset_vulnerability.finding_id', ondelete='CASCADE'),
                                nullable=True)
    action = Column(String(16), nullable=False)
    # {"remediation_status": {"from": "affected", "to": "patched"}, …}
    changes = Column(JSONB)
    reason = Column(Text)
    decision_id = Column(BigInteger, ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'),
                         nullable=True)
    changed_by_id = Column(BigInteger, ForeignKey('user.id', ondelete='SET NULL'), nullable=True)
    changed_at = Column(DateTime, nullable=False, server_default=text('now()'))

    __table_args__ = (
        CheckConstraint('num_nonnulls(case_finding_id, managed_finding_id) = 1',
                        name='ck_vulnerability_finding_history_target'),
        CheckConstraint(f'action IN ({_sql_in_list(FINDING_HISTORY_ACTIONS)})',
                        name='ck_vulnerability_finding_history_action'),
        Index('idx_vuln_finding_history_case', 'case_finding_id', 'changed_at'),
        Index('idx_vuln_finding_history_managed', 'managed_finding_id', 'changed_at'),
    )


class CaseAssetVulnerabilityEvent(db.Model):
    """Timeline event evidencing a case finding (exploitation, scan…)."""
    __tablename__ = 'case_asset_vulnerability_event'

    finding_id = Column(BigInteger, ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'),
                        primary_key=True)
    event_id = Column(BigInteger, ForeignKey('cases_events.event_id', ondelete='CASCADE'),
                      primary_key=True, index=True)


class CaseAssetVulnerabilityIoc(db.Model):
    """IOC tied to a case finding (exploitation indicator…)."""
    __tablename__ = 'case_asset_vulnerability_ioc'

    finding_id = Column(BigInteger, ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'),
                        primary_key=True)
    ioc_id = Column(BigInteger, ForeignKey('ioc.ioc_id', ondelete='CASCADE'), primary_key=True, index=True)
