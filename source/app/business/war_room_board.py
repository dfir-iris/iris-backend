#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.

"""War-room board: cross-case asset status / decision overview.

The blueprint decides which attached cases the caller can read and
passes them in; cases outside that set only contribute to the
`cases` KPI and appear as `{case_id, accessible: False}`. Nothing else
about them (name, customer, asset counts) is ever loaded.

KPI definitions (over the accessible cases):
  * assets       — every case asset
  * compromised  — `asset_compromise_status_id == compromised`
  * flagged      — assets carrying at least one status flag
  * done / exceptions — assets carrying at least one flag of kind
                   `done` / `exception`
  * unflagged    — assets with no flag
  * decisions_*  — open = proposed/approved and not implemented
  * tasks_open   — open war-room tasks (per-case open case tasks are in
                   each case entry)

`decisions` lists every open decision of the room (they are war-room
level, not case level, so no case filtering applies).
"""

import datetime

from app.business.vulnerability_findings import vulnerability_findings_attention
from app.business.vulnerability_findings import vulnerability_findings_summary
from app.business.asset_flags import asset_flags_serialize
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_asset_counts
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_attached_case_ids
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_case_rows
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_compromised_unflagged
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_exceptions_without_decision
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_flag_counts
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_flags
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_kind_counts
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_open_decisions
from app.datamgmt.war_rooms.war_room_board_db import war_room_board_db_open_war_room_tasks_count


ATTENTION_LIMIT = 100
_DUE_SOON = datetime.timedelta(hours=24)
_SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2}


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def war_room_board_attached_case_ids(war_room_id):
    return war_room_board_db_attached_case_ids(war_room_id)


def _aggregate_counts(asset_rows, flag_rows, kind_rows):
    """Fold the per-case asset, per-(case, flag) and per-(case, kind) rows
    into per-case `by_flag` / `by_kind` maps and asset totals.

    An asset carrying several flags counts under each of them, so
    `by_flag` and `by_kind` do not add up to the asset total; `none` is
    the number of assets without any flag."""
    per_case = {}
    for row in asset_rows:
        entry = per_case.setdefault(row.case_id, _empty_counts())
        total = int(row.total or 0)
        unflagged = total - int(row.flagged or 0)
        entry['assets_total'] = total
        entry['assets_compromised'] = int(row.compromised or 0)
        entry['by_flag']['none'] = unflagged
        entry['by_kind']['none'] = unflagged
    for row in flag_rows:
        entry = per_case.setdefault(row.case_id, _empty_counts())
        entry['by_flag'][str(row.flag_id)] = int(row.total or 0)
    for row in kind_rows:
        entry = per_case.setdefault(row.case_id, _empty_counts())
        if row.kind in entry['by_kind']:
            entry['by_kind'][row.kind] = int(row.total or 0)
    return per_case


def _empty_counts():
    return {
        'assets_total': 0,
        'assets_compromised': 0,
        'by_flag': {},
        'by_kind': {'none': 0, 'status': 0, 'done': 0, 'exception': 0},
    }


def _decision_ref(row):
    return f'D-{row.number}'


def _decision_attention(row, now):
    """One attention item per open decision, for its most pressing reason."""
    ref = _decision_ref(row)
    if row.target_at is not None and row.target_at < now:
        return 'decision_overdue', 'high', f'{ref} is overdue: {row.title}'
    if row.target_at is not None and row.target_at <= now + _DUE_SOON:
        return 'decision_due_soon', 'medium', f'{ref} is due within 24 h: {row.title}'
    if row.status == 'proposed':
        pending = int(row.pending_approvers or 0)
        if pending > 0:
            return 'decision_pending_vote', 'low', f'{ref} awaits {pending} vote(s): {row.title}'
        return 'decision_pending_approval', 'low', f'{ref} awaits approval: {row.title}'
    return 'decision_pending_implementation', 'low', f'{ref} is approved, not implemented yet: {row.title}'


def _vulnerability_attention(vulnerabilities):
    items = []
    for row in vulnerabilities.get('exploited_open', []):
        items.append({
            'type': 'vulnerability_exploited_open', 'severity': 'high',
            'label': f'{row["asset_name"]} was exploited through {row["identifier"]} and is not fixed',
            'case_id': row['case_id'], 'asset_id': row['asset_id'], 'finding_id': row['finding_id'],
        })
    for row in vulnerabilities.get('overdue', []):
        items.append({
            'type': 'vulnerability_overdue', 'severity': 'medium',
            'label': f'{row["identifier"]} on {row["asset_name"]} is past its remediation due date',
            'case_id': row['case_id'], 'asset_id': row['asset_id'], 'finding_id': row['finding_id'],
        })
    return items


def war_room_board_attention(compromised_rows, exception_rows, decision_rows, now, limit=ATTENTION_LIMIT,
                             vulnerabilities=None):
    """Build the "needs attention" list, most severe first, capped.

    Every open decision appears exactly once; assets that are compromised
    and carry no flag, or carry an exception flag without a backing
    decision, appear too, as do vulnerability findings exploited and not
    fixed, or past their due date (`vulnerabilities` is the output of
    `vulnerability_findings_attention`)."""
    items = []
    for row in decision_rows:
        kind, severity, label = _decision_attention(row, now)
        items.append({'type': kind, 'severity': severity, 'label': label, 'decision_id': row.decision_id})
    for row in compromised_rows:
        items.append({
            'type': 'compromised_unflagged', 'severity': 'high',
            'label': f'{row.asset_name} is compromised and has no status flag',
            'case_id': row.case_id, 'asset_id': row.asset_id,
        })
    for row in exception_rows:
        items.append({
            'type': 'exception_without_decision', 'severity': 'medium',
            'label': f'{row.asset_name} is flagged {row.flag_name} without a backing decision',
            'case_id': row.case_id, 'asset_id': row.asset_id,
        })
    items.extend(_vulnerability_attention(vulnerabilities or {}))
    # Stable sort: decisions stay ahead of assets within a severity.
    items.sort(key=lambda i: _SEVERITY_RANK[i['severity']])
    return items[:limit]


def _serialize_decision(row, now):
    target_at = row.target_at
    return {
        'decision_id': row.decision_id,
        'number': row.number,
        'ref': _decision_ref(row),
        'title': row.title,
        'status': row.status,
        'target_at': target_at.isoformat() if target_at else None,
        'overdue': target_at is not None and target_at < now,
        'due_soon': target_at is not None and now <= target_at <= now + _DUE_SOON,
        'pending_approvers': int(row.pending_approvers or 0),
    }


def _decision_kpis(decision_rows, now):
    overdue = 0
    due_soon = 0
    for row in decision_rows:
        if row.target_at is None:
            continue
        if row.target_at < now:
            overdue += 1
        elif row.target_at <= now + _DUE_SOON:
            due_soon += 1
    return len(decision_rows), overdue, due_soon


def war_room_board_build(war_room_id, accessible_case_ids, now=None, include_vulnerabilities=True):
    """Return the board payload. `accessible_case_ids` MUST already be
    restricted to attached cases the caller can read. Without
    `include_vulnerabilities` (caller lacks the vulnerability read
    permission) the vulnerability KPIs and attention items are omitted."""
    now = now or _utcnow()
    attached = war_room_board_db_attached_case_ids(war_room_id)
    allowed = set(accessible_case_ids or ())
    accessible = [c for c in attached if c in allowed]

    flags = war_room_board_db_flags()
    case_rows = {r.case_id: r for r in war_room_board_db_case_rows(accessible)}
    per_case = _aggregate_counts(war_room_board_db_asset_counts(accessible),
                                 war_room_board_db_flag_counts(accessible),
                                 war_room_board_db_kind_counts(accessible))
    decision_rows = war_room_board_db_open_decisions(war_room_id)

    cases = []
    totals = _empty_counts()
    for case_id in attached:
        row = case_rows.get(case_id)
        if case_id not in allowed or row is None:
            cases.append({'case_id': case_id, 'accessible': False})
            continue
        counts = per_case.get(case_id, _empty_counts())
        totals['assets_total'] += counts['assets_total']
        totals['assets_compromised'] += counts['assets_compromised']
        for kind, value in counts['by_kind'].items():
            totals['by_kind'][kind] += value
        cases.append({
            'case_id': case_id,
            'accessible': True,
            'case_name': row.case_name,
            'customer_id': row.customer_id,
            'customer_name': row.customer_name,
            'state_id': row.state_id,
            'state_name': row.state_name,
            'owner_id': row.owner_id,
            'owner_name': row.owner_name,
            'severity_name': row.severity_name,
            'assets_total': counts['assets_total'],
            'assets_compromised': counts['assets_compromised'],
            'by_flag': counts['by_flag'],
            'by_kind': counts['by_kind'],
            'tasks_open': int(row.tasks_open or 0),
        })

    decisions_open, decisions_overdue, decisions_due_24h = _decision_kpis(decision_rows, now)
    by_kind = totals['by_kind']
    kpis = {
        'cases': len(attached),
        'cases_accessible': len([c for c in cases if c['accessible']]),
        'assets': totals['assets_total'],
        'compromised': totals['assets_compromised'],
        'flagged': totals['assets_total'] - by_kind['none'],
        'done': by_kind['done'],
        'exceptions': by_kind['exception'],
        'unflagged': by_kind['none'],
        'decisions_open': decisions_open,
        'decisions_overdue': decisions_overdue,
        'decisions_due_24h': decisions_due_24h,
        'tasks_open': war_room_board_db_open_war_room_tasks_count(war_room_id),
    }

    accessible_with_rows = [c['case_id'] for c in cases if c['accessible']]
    vulnerability_attention = None
    if include_vulnerabilities:
        vulnerability_summary = vulnerability_findings_summary(accessible_with_rows)
        kpis.update({
            'vulnerabilities_open': vulnerability_summary['open'],
            'vulnerabilities_exploited_open': vulnerability_summary['exploited_open'],
            'vulnerabilities_overdue': vulnerability_summary['overdue'],
            'vulnerabilities_kev_open': vulnerability_summary['kev_open'],
            'vulnerable_assets': vulnerability_summary['assets_open'],
        })
        vulnerability_attention = vulnerability_findings_attention(accessible_with_rows, ATTENTION_LIMIT)
    attention = war_room_board_attention(
        war_room_board_db_compromised_unflagged(accessible_with_rows, ATTENTION_LIMIT),
        war_room_board_db_exceptions_without_decision(accessible_with_rows, ATTENTION_LIMIT),
        decision_rows,
        now,
        vulnerabilities=vulnerability_attention,
    )

    return {
        'generated_at': now.isoformat(),
        'kpis': kpis,
        'flags': [asset_flags_serialize(f) for f in flags],
        'cases': cases,
        'attention': attention,
        'decisions': [_serialize_decision(r, now) for r in decision_rows],
    }
