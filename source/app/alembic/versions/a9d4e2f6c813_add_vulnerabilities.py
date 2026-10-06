"""Vulnerability tracking — catalogue, case / registry findings, history, evidence.

- `vulnerability` (+ `vulnerability_alias`): instance-wide catalogue,
  public (CVE, GHSA, vendor advisory) or private to IRIS
  (`IRIS-VULN-YYYY-NNNN`).
- `case_asset_vulnerability`: a case asset affected by a catalogue entry,
  with its remediation and exploitation status.
- `managed_asset_vulnerability`: the same on a registry asset.
- `vulnerability_finding_history`: append-only change log of both.
- `case_asset_vulnerability_event` / `_ioc`: evidence of a case finding.
- `vulnerabilities_write` permission (0x10000000), granted to the groups
  that already hold `server_administrator`: `post_init` only seeds the
  mask of a group on the boot that creates it, so an existing
  administrator group would otherwise never see the new bit.

Idempotent via `_has_table` / `index_exists`: `db.create_all()` runs
before the migrations on a fresh install and already creates the tables.

Revision ID: a9d4e2f6c813
Revises: f3c7a9e1b5d2
Create Date: 2026-10-06 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic.alembic_utils import _has_table
from app.alembic.alembic_utils import index_exists


revision = 'a9d4e2f6c813'
down_revision = 'f3c7a9e1b5d2'
branch_labels = None
depends_on = None


_PERM_SERVER_ADMINISTRATOR = 0x2
_PERM_VULNERABILITIES_WRITE = 0x10000000

_REMEDIATION_STATUSES = ("'under-analysis','affected','mitigated','patched','verified',"
                         "'not-affected','risk-accepted','false-positive'")
_EXPLOITATION_STATUSES = "'unknown','not-exploited','attempted','suspected','exploited'"
_JUSTIFICATIONS = ("'component_not_present','vulnerable_code_not_present',"
                   "'vulnerable_code_not_in_execute_path',"
                   "'vulnerable_code_cannot_be_controlled_by_adversary',"
                   "'inline_mitigations_already_exist'")


def _create_index(name, table, columns):
    if not index_exists(table, name):
        op.create_index(name, table, columns)


def _drop_table(table):
    if _has_table(table):
        op.drop_table(table)


def _user_fk(name):
    return sa.Column(name, sa.BigInteger(), sa.ForeignKey('user.id', ondelete='SET NULL'), nullable=True)


def _finding_columns():
    return [
        sa.Column('vulnerability_id', sa.BigInteger(),
                  sa.ForeignKey('vulnerability.vulnerability_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('remediation_status', sa.String(length=24), nullable=False,
                  server_default=sa.text("'under-analysis'")),
        sa.Column('not_affected_justification', sa.String(length=64), nullable=True),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('exploitation_status', sa.String(length=16), nullable=False,
                  server_default=sa.text("'unknown'")),
        sa.Column('exploited_at', sa.DateTime(), nullable=True),
        sa.Column('detection_source', sa.String(length=128), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=True),
        sa.Column('component', sa.Text(), nullable=True),
        sa.Column('installed_version', sa.Text(), nullable=True),
        sa.Column('fixed_version', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('verification_method', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        _user_fk('owner_id'),
        _user_fk('verified_by_id'),
        _user_fk('created_by_id'),
        _user_fk('updated_by_id'),
    ]


def _finding_checks(prefix):
    return [
        sa.CheckConstraint(f'remediation_status IN ({_REMEDIATION_STATUSES})',
                           name=f'ck_{prefix}_remediation_status'),
        sa.CheckConstraint(f'exploitation_status IN ({_EXPLOITATION_STATUSES})',
                           name=f'ck_{prefix}_exploitation_status'),
        sa.CheckConstraint(f'not_affected_justification IS NULL OR not_affected_justification IN ({_JUSTIFICATIONS})',
                           name=f'ck_{prefix}_justification'),
        sa.CheckConstraint("remediation_status <> 'risk-accepted' OR length(btrim(coalesce(status_reason, ''))) > 0",
                           name=f'ck_{prefix}_risk_reason'),
    ]


def _upgrade_catalogue():
    if not _has_table('vulnerability'):
        op.create_table(
            'vulnerability',
            sa.Column('vulnerability_id', sa.BigInteger(), primary_key=True),
            sa.Column('vulnerability_uuid', sa.UUID(), nullable=False,
                      server_default=sa.text('gen_random_uuid()')),
            sa.Column('identifier', sa.String(length=64), nullable=False),
            sa.Column('is_private', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('kind', sa.String(length=32), nullable=False, server_default=sa.text("'other'")),
            sa.Column('title', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('cvss_version', sa.String(length=8), nullable=True),
            sa.Column('cvss_vector', sa.Text(), nullable=True),
            sa.Column('cvss_score', sa.Float(), nullable=True),
            sa.Column('severity', sa.String(length=16), nullable=False, server_default=sa.text("'unknown'")),
            sa.Column('epss_score', sa.Float(), nullable=True),
            sa.Column('epss_percentile', sa.Float(), nullable=True),
            sa.Column('epss_date', sa.Date(), nullable=True),
            sa.Column('kev', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('kev_date_added', sa.Date(), nullable=True),
            sa.Column('kev_due_date', sa.Date(), nullable=True),
            sa.Column('kev_ransomware', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('exploit_maturity', sa.String(length=16), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column('patch_availability', sa.String(length=16), nullable=False,
                      server_default=sa.text("'unknown'")),
            sa.Column('cwes', postgresql.JSONB(), nullable=True),
            sa.Column('affected_products', postgresql.JSONB(), nullable=True),
            sa.Column('reference_urls', postgresql.JSONB(), nullable=True),
            sa.Column('published_at', sa.Date(), nullable=True),
            sa.Column('modified_at', sa.Date(), nullable=True),
            sa.Column('tlp_id', sa.Integer(), sa.ForeignKey('tlp.tlp_id', ondelete='SET NULL'), nullable=True),
            sa.Column('tags', sa.Text(), nullable=True),
            sa.Column('source', sa.String(length=16), nullable=False, server_default=sa.text("'manual'")),
            sa.Column('enrichment', postgresql.JSONB(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            _user_fk('created_by_id'),
            _user_fk('updated_by_id'),
            sa.UniqueConstraint('identifier', name='uq_vulnerability_identifier'),
            sa.UniqueConstraint('vulnerability_uuid', name='uq_vulnerability_uuid'),
            sa.CheckConstraint("kind IN ('cve','advisory','misconfiguration','weak-credentials',"
                               "'exposed-service','design-flaw','zero-day','other')",
                               name='ck_vulnerability_kind'),
            sa.CheckConstraint("severity IN ('critical','high','medium','low','none','unknown')",
                               name='ck_vulnerability_severity'),
            sa.CheckConstraint("exploit_maturity IN ('unknown','none','poc','weaponized','in-the-wild')",
                               name='ck_vulnerability_exploit_maturity'),
            sa.CheckConstraint("patch_availability IN ('unknown','patch','workaround','none')",
                               name='ck_vulnerability_patch_availability'),
            sa.CheckConstraint("source IN ('manual','module','import')", name='ck_vulnerability_source'),
            sa.CheckConstraint("cvss_version IS NULL OR cvss_version IN ('2.0','3.0','3.1','4.0')",
                               name='ck_vulnerability_cvss_version'),
            sa.CheckConstraint('cvss_score IS NULL OR (cvss_score >= 0 AND cvss_score <= 10)',
                               name='ck_vulnerability_cvss_score'),
            sa.CheckConstraint('epss_score IS NULL OR (epss_score >= 0 AND epss_score <= 1)',
                               name='ck_vulnerability_epss_score'),
            sa.CheckConstraint('epss_percentile IS NULL OR (epss_percentile >= 0 AND epss_percentile <= 1)',
                               name='ck_vulnerability_epss_percentile'),
            sa.CheckConstraint("is_private = (identifier LIKE 'IRIS-VULN-%')",
                               name='ck_vulnerability_private_identifier'),
        )
    _create_index('idx_vulnerability_severity', 'vulnerability', ['severity'])
    _create_index('idx_vulnerability_updated', 'vulnerability', ['updated_at'])

    if not _has_table('vulnerability_alias'):
        op.create_table(
            'vulnerability_alias',
            sa.Column('alias_id', sa.BigInteger(), primary_key=True),
            sa.Column('vulnerability_id', sa.BigInteger(),
                      sa.ForeignKey('vulnerability.vulnerability_id', ondelete='CASCADE'), nullable=False),
            sa.Column('alias', sa.String(length=64), nullable=False),
            sa.UniqueConstraint('alias', name='uq_vulnerability_alias'),
        )
    _create_index('ix_vulnerability_alias_vulnerability_id', 'vulnerability_alias', ['vulnerability_id'])


def _upgrade_findings():
    if not _has_table('case_asset_vulnerability'):
        op.create_table(
            'case_asset_vulnerability',
            sa.Column('finding_id', sa.BigInteger(), primary_key=True),
            sa.Column('finding_uuid', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
            sa.Column('asset_id', sa.BigInteger(),
                      sa.ForeignKey('case_assets.asset_id', ondelete='CASCADE'), nullable=False),
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            *_finding_columns(),
            sa.UniqueConstraint('asset_id', 'vulnerability_id', name='uq_case_asset_vulnerability'),
            *_finding_checks('case_asset_vuln'),
        )
    _create_index('idx_case_asset_vuln_asset', 'case_asset_vulnerability', ['asset_id'])
    _create_index('idx_case_asset_vuln_status', 'case_asset_vulnerability', ['remediation_status'])
    _create_index('ix_case_asset_vulnerability_vulnerability_id', 'case_asset_vulnerability',
                  ['vulnerability_id'])

    if not _has_table('managed_asset_vulnerability'):
        op.create_table(
            'managed_asset_vulnerability',
            sa.Column('finding_id', sa.BigInteger(), primary_key=True),
            sa.Column('finding_uuid', sa.UUID(), nullable=False, server_default=sa.text('gen_random_uuid()')),
            sa.Column('managed_asset_id', sa.BigInteger(),
                      sa.ForeignKey('managed_asset.managed_asset_id', ondelete='CASCADE'), nullable=False),
            *_finding_columns(),
            sa.UniqueConstraint('managed_asset_id', 'vulnerability_id', name='uq_managed_asset_vulnerability'),
            *_finding_checks('managed_asset_vuln'),
        )
    _create_index('idx_managed_asset_vuln_asset', 'managed_asset_vulnerability', ['managed_asset_id'])
    _create_index('ix_managed_asset_vulnerability_vulnerability_id', 'managed_asset_vulnerability',
                  ['vulnerability_id'])

    if not _has_table('vulnerability_finding_history'):
        op.create_table(
            'vulnerability_finding_history',
            sa.Column('history_id', sa.BigInteger(), primary_key=True),
            sa.Column('case_finding_id', sa.BigInteger(),
                      sa.ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'), nullable=True),
            sa.Column('managed_finding_id', sa.BigInteger(),
                      sa.ForeignKey('managed_asset_vulnerability.finding_id', ondelete='CASCADE'),
                      nullable=True),
            sa.Column('action', sa.String(length=16), nullable=False),
            sa.Column('changes', postgresql.JSONB(), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('decision_id', sa.BigInteger(),
                      sa.ForeignKey('war_room_decision.decision_id', ondelete='SET NULL'), nullable=True),
            _user_fk('changed_by_id'),
            sa.Column('changed_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint('num_nonnulls(case_finding_id, managed_finding_id) = 1',
                               name='ck_vulnerability_finding_history_target'),
            sa.CheckConstraint("action IN ('create','update')", name='ck_vulnerability_finding_history_action'),
        )
    _create_index('idx_vuln_finding_history_case', 'vulnerability_finding_history',
                  ['case_finding_id', 'changed_at'])
    _create_index('idx_vuln_finding_history_managed', 'vulnerability_finding_history',
                  ['managed_finding_id', 'changed_at'])

    if not _has_table('case_asset_vulnerability_event'):
        op.create_table(
            'case_asset_vulnerability_event',
            sa.Column('finding_id', sa.BigInteger(),
                      sa.ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('event_id', sa.BigInteger(),
                      sa.ForeignKey('cases_events.event_id', ondelete='CASCADE'), primary_key=True),
        )
    _create_index('ix_case_asset_vulnerability_event_event_id', 'case_asset_vulnerability_event', ['event_id'])

    if not _has_table('case_asset_vulnerability_ioc'):
        op.create_table(
            'case_asset_vulnerability_ioc',
            sa.Column('finding_id', sa.BigInteger(),
                      sa.ForeignKey('case_asset_vulnerability.finding_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('ioc_id', sa.BigInteger(),
                      sa.ForeignKey('ioc.ioc_id', ondelete='CASCADE'), primary_key=True),
        )
    _create_index('ix_case_asset_vulnerability_ioc_ioc_id', 'case_asset_vulnerability_ioc', ['ioc_id'])


def upgrade():
    _upgrade_catalogue()
    _upgrade_findings()

    if _has_table('groups'):
        op.execute(sa.text(
            f'UPDATE groups SET group_permissions = group_permissions | {_PERM_VULNERABILITIES_WRITE} '
            f'WHERE group_permissions & {_PERM_SERVER_ADMINISTRATOR} != 0'
        ))


def downgrade():
    # Cleared everywhere: once the tables are gone the bit denotes a
    # feature that no longer exists.
    if _has_table('groups'):
        op.execute(sa.text(
            f'UPDATE groups SET group_permissions = group_permissions & ~{_PERM_VULNERABILITIES_WRITE}'
        ))

    _drop_table('case_asset_vulnerability_ioc')
    _drop_table('case_asset_vulnerability_event')
    _drop_table('vulnerability_finding_history')
    _drop_table('managed_asset_vulnerability')
    _drop_table('case_asset_vulnerability')
    _drop_table('vulnerability_alias')
    _drop_table('vulnerability')
