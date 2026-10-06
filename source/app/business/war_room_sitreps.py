#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Versioned situational reports."""

import datetime

from app.business.war_rooms import war_room_get
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_attached_case_ids
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_case_rows
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_cases_attached_since
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_decisions
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_exception_assets
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_last_published_at
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_open_tasks
from app.datamgmt.war_rooms.war_room_sitreps_db import sitreps_db_stage_transitions
from app.db import db
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomCase
from app.models.war_rooms import WarRoomSitRep
from app.models.war_rooms import WarRoomTask


def _next_version(war_room_id):
    latest_row = (
        WarRoomSitRep.query
        .with_entities(WarRoomSitRep.version)
        .filter(WarRoomSitRep.war_room_id == war_room_id)
        .order_by(WarRoomSitRep.version.desc())
        .first()
    )
    latest = latest_row.version if latest_row else 0
    return latest + 1


def _snapshot(war_room_id):
    """Capture the war-room state we want frozen in this SitRep.

    Keeps the report coherent later even if the underlying data drifts
    or the case is detached.
    """
    attached = (
        WarRoomCase.query
        .with_entities(WarRoomCase.case_id)
        .filter(WarRoomCase.war_room_id == war_room_id)
        .all()
    )
    open_tasks = (
        WarRoomTask.query
        .filter(WarRoomTask.war_room_id == war_room_id,
                WarRoomTask.closed_at == None)
        .count()
    )
    closed_tasks = (
        WarRoomTask.query
        .filter(WarRoomTask.war_room_id == war_room_id,
                WarRoomTask.closed_at != None)
        .count()
    )
    return {
        'attached_case_ids': [a.case_id for a in attached],
        'tasks_open': open_tasks,
        'tasks_closed': closed_tasks,
        'captured_at': datetime.datetime.utcnow().isoformat(),
    }


def sitrep_list(war_room_id):
    return (
        WarRoomSitRep.query
        .filter(WarRoomSitRep.war_room_id == war_room_id)
        .order_by(WarRoomSitRep.version.desc())
        .all()
    )


def sitrep_get(war_room_id, sitrep_id):
    row = WarRoomSitRep.query.filter_by(
        war_room_id=war_room_id, sitrep_id=sitrep_id
    ).first()
    if row is None:
        raise ObjectNotFoundError()
    return row


def sitrep_draft(war_room_id, title, body_md='', authored_by_id=None):
    if not isinstance(title, str) or not title.strip():
        raise BusinessProcessingError('SitRep title is required')
    sit = WarRoomSitRep()
    sit.war_room_id = war_room_id
    sit.version = _next_version(war_room_id)
    sit.title = title.strip()[:512]
    sit.body_md = body_md or ''
    sit.authored_by_id = authored_by_id
    sit.snapshot_json = None
    sit.published = False
    db.session.add(sit)
    db.session.commit()
    track_activity(f'drafted sitrep "{sit.title}" (v{sit.version})',
                   war_room_id=war_room_id)
    sit = call_modules_hook('on_postload_war_room_sitrep_create', sit)
    return sit


def sitrep_update(war_room_id, sitrep_id, title=None, body_md=None):
    # `published` is a state marker, not a write-lock — an IC needs to
    # be able to correct a published SitRep (typo, updated facts) after
    # the fact. The snapshot captured at publish time stays as it was
    # (it represents "what this report claimed at publication"), only
    # the free-text body and title are editable here.
    sit = sitrep_get(war_room_id, sitrep_id)
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            raise BusinessProcessingError('SitRep title is required')
        sit.title = title.strip()[:512]
    if body_md is not None:
        sit.body_md = body_md
    db.session.commit()
    track_activity(f'updated sitrep "{sit.title}" (v{sit.version})',
                   war_room_id=war_room_id)
    sit = call_modules_hook('on_postload_war_room_sitrep_update', sit)
    return sit


def sitrep_publish(war_room_id, sitrep_id):
    """Freeze the SitRep at the current war-room state.

    Publishing snapshots the attached cases + task counts so the
    report stays coherent later, and forbids further edits.
    """
    sit = sitrep_get(war_room_id, sitrep_id)
    if sit.published:
        raise BusinessProcessingError('SitRep is already published')
    sit.snapshot_json = _snapshot(war_room_id)
    sit.published = True
    sit.authored_at = datetime.datetime.utcnow()
    db.session.commit()
    track_activity(f'published sitrep "{sit.title}" (v{sit.version})',
                   war_room_id=war_room_id)
    sit = call_modules_hook('on_postload_war_room_sitrep_publish', sit)
    return sit


def sitrep_delete(war_room_id, sitrep_id):
    # Published SitReps can still be deleted — matches the "publishing
    # is just a state" contract. Callers (UI) should confirm loudly for
    # published ones since a delete removes the record entirely; the
    # chat "SitRep published" mirror-message stays but its ref now
    # points at nothing, which the chat surface handles as a dead ref.
    sit = sitrep_get(war_room_id, sitrep_id)
    label = f'"{sit.title}" (v{sit.version})'
    db.session.delete(sit)
    db.session.commit()
    track_activity(f'deleted sitrep {label}', war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_sitrep_delete',
                      {'war_room_id': war_room_id, 'sitrep_id': sitrep_id})


def sitrep_as_markdown(sit):
    """Render the SitRep as standalone markdown for download."""
    snap = sit.snapshot_json or {}
    lines = [
        f'# {sit.title}',
        '',
        f'_War room #{sit.war_room_id} — version {sit.version}_',
        '',
    ]
    if sit.authored_at:
        lines.append(f'**Authored at:** {sit.authored_at.isoformat()}')
    if sit.published:
        lines.append('**Status:** Published')
    else:
        lines.append('**Status:** Draft')
    lines.append('')
    if snap:
        lines.append('## Snapshot at publish')
        if 'attached_case_ids' in snap:
            ids = snap['attached_case_ids'] or []
            lines.append(f'- Attached cases: {", ".join(str(i) for i in ids) or "—"}')
        if 'tasks_open' in snap:
            lines.append(f'- Open tasks: {snap.get("tasks_open", 0)}')
        if 'tasks_closed' in snap:
            lines.append(f'- Closed tasks: {snap.get("tasks_closed", 0)}')
        lines.append('')
    lines.append('## Report')
    lines.append('')
    lines.append(sit.body_md or '_(no content)_')
    return '\n'.join(lines)


def sitrep_as_html(sit):
    """Render the SitRep as a stand-alone HTML page.

    Uses the same markdown→HTML pipeline IRIS already relies on for
    case notes so the output matches the rest of the product. Used by
    the export-to-PDF endpoint which feeds this into wkhtmltopdf when
    it's available, or returns the HTML directly for print-to-PDF.
    """
    try:
        import markdown
        body = markdown.markdown(
            sitrep_as_markdown(sit),
            extensions=['tables', 'fenced_code']
        )
    except Exception:
        body = '<pre>' + (sitrep_as_markdown(sit)
                          .replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;')) + '</pre>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{sit.title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            max-width: 800px; margin: 2em auto; padding: 0 1em; color: #1f2937; }}
    h1, h2, h3 {{ color: #111827; }}
    code, pre {{ background: #f3f4f6; padding: 2px 4px; border-radius: 3px; }}
    pre {{ padding: 12px; overflow-x: auto; }}
    table {{ border-collapse: collapse; }}
    th, td {{ border: 1px solid #d1d5db; padding: 4px 8px; }}
    blockquote {{ border-left: 4px solid #94a3b8; margin: 1em 0; padding: 0 1em; color: #475569; }}
  </style>
</head>
<body>{body}</body>
</html>
"""


# --- Cadence ----------------------------------------------------------------

_CADENCE_MIN_MINUTES = 15
_CADENCE_MAX_MINUTES = 10080
_UNSET = object()


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def sitrep_cadence_validate(cadence_minutes, reminder_minutes):
    """Validate a cadence/reminder pair; returns the normalised pair.

    No cadence means no reminder either.
    """
    if cadence_minutes is None:
        return None, None
    if not _is_int(cadence_minutes) or \
            not _CADENCE_MIN_MINUTES <= cadence_minutes <= _CADENCE_MAX_MINUTES:
        raise BusinessProcessingError(
            f'cadence_minutes must be an integer between {_CADENCE_MIN_MINUTES} '
            f'and {_CADENCE_MAX_MINUTES}, or null'
        )
    if reminder_minutes is None:
        return cadence_minutes, None
    if not _is_int(reminder_minutes) or not 0 <= reminder_minutes <= cadence_minutes:
        raise BusinessProcessingError(
            'reminder_minutes must be an integer between 0 and cadence_minutes, or null'
        )
    return cadence_minutes, reminder_minutes


def sitrep_cadence_compute(cadence_minutes, reminder_minutes, last_published_at,
                           war_room_created_at, now):
    """Pure computation of the cadence state."""
    next_due_at = None
    if cadence_minutes:
        base = last_published_at or war_room_created_at
        if base is not None:
            next_due_at = base + datetime.timedelta(minutes=cadence_minutes)
    return {
        'cadence_minutes': cadence_minutes,
        'reminder_minutes': reminder_minutes,
        'last_published_at': last_published_at.isoformat() if last_published_at else None,
        'next_due_at': next_due_at.isoformat() if next_due_at else None,
        'is_overdue': bool(next_due_at is not None and next_due_at < now),
    }


def sitrep_cadence_get(war_room_id):
    war_room = war_room_get(war_room_id)
    return sitrep_cadence_compute(
        war_room.sitrep_cadence_minutes,
        war_room.sitrep_reminder_minutes,
        sitreps_db_last_published_at(war_room_id),
        war_room.created_at,
        datetime.datetime.utcnow(),
    )


def sitrep_cadence_set(war_room_id, cadence_minutes, reminder_minutes=_UNSET):
    """Set the cadence. An omitted reminder keeps the current one when it
    still fits within the new cadence, else it is cleared."""
    war_room = war_room_get(war_room_id)
    if reminder_minutes is _UNSET:
        reminder_minutes = war_room.sitrep_reminder_minutes
        if cadence_minutes is None or (
            _is_int(cadence_minutes) and reminder_minutes is not None
            and reminder_minutes > cadence_minutes
        ):
            reminder_minutes = None
    cadence_minutes, reminder_minutes = sitrep_cadence_validate(cadence_minutes, reminder_minutes)
    war_room.sitrep_cadence_minutes = cadence_minutes
    war_room.sitrep_reminder_minutes = reminder_minutes
    db.session.commit()
    if cadence_minutes is None:
        track_activity('cleared the sitrep cadence', war_room_id=war_room_id)
    else:
        track_activity(f'set the sitrep cadence to {cadence_minutes} minutes',
                       war_room_id=war_room_id)
    return sitrep_cadence_get(war_room_id)


# --- Auto-draft ---------------------------------------------------------------

_DRAFT_MAX_TRANSITIONS = 200
_DRAFT_MAX_EXCEPTIONS = 200
_DRAFT_MAX_TASKS = 25
_DECISION_OPEN_STATUSES = ('proposed', 'approved')
_MD_SPECIAL_CHARS = '\\`*_[]<>|#~'


def sitrep_md_escape(value):
    """Make a user string safe for inline markdown and table cells."""
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return ''.join(f'\\{c}' if c in _MD_SPECIAL_CHARS else c for c in text)


def _fmt_dt(value):
    return value.strftime('%Y-%m-%d %H:%M') if value else '—'


def _iso(value):
    return value.isoformat() if value else None


def sitrep_attached_case_ids(war_room_id):
    return sitreps_db_attached_case_ids(war_room_id)


def _decision_ref(number):
    return f'D-{number}'


def _decision_is_open(row):
    return row.status in _DECISION_OPEN_STATUSES and row.implemented_at is None


def _collect_changes(war_room_id, case_ids, since, decisions):
    changes = []
    for row in sitreps_db_stage_transitions(case_ids, since, _DRAFT_MAX_TRANSITIONS):
        changes.append({
            'type': 'stage', 'at': _iso(row.changed_at),
            'case_id': row.case_id, 'case_name': row.case_name,
            'asset_id': row.asset_id, 'asset_name': row.asset_name,
            'from_stage_name': row.from_stage_name, 'to_stage_name': row.to_stage_name,
            'reason': row.reason, 'changed_by_name': row.changed_by_name,
        })
    for row in decisions:
        events = [('decision_created', row.created_at),
                  ('decision_implemented', row.implemented_at)]
        if row.status in ('approved', 'rejected'):
            events.append((f'decision_{row.status}', row.decided_at))
        for kind, at in events:
            if at is None or (since is not None and at <= since):
                continue
            changes.append({'type': kind, 'at': _iso(at), 'decision_id': row.decision_id,
                            'ref': _decision_ref(row.number), 'title': row.title})
    for row in sitreps_db_cases_attached_since(war_room_id, case_ids, since):
        changes.append({'type': 'case_attached', 'at': _iso(row.attached_at),
                        'case_id': row.case_id, 'case_name': row.case_name})
    changes.sort(key=lambda c: c['at'] or '', reverse=True)
    return changes


def sitrep_auto_draft_sections(war_room_id, readable_case_ids, since, now):
    """Collect the draft data from the readable cases only."""
    readable = set(readable_case_ids)
    case_ids = [cid for cid in sitreps_db_attached_case_ids(war_room_id) if cid in readable]
    decisions = sitreps_db_decisions(war_room_id)
    soon = now + datetime.timedelta(hours=24)

    cases = [{
        'case_id': row.case_id, 'case_name': row.case_name,
        'customer_name': row.customer_name, 'state_name': row.state_name,
        'assets_total': int(row.assets_total or 0), 'assets_done': int(row.assets_done or 0),
        'assets_exception': int(row.assets_exception or 0),
        'tasks_open': int(row.tasks_open or 0),
    } for row in sitreps_db_case_rows(war_room_id, case_ids)]

    open_decisions = [{
        'decision_id': row.decision_id, 'ref': _decision_ref(row.number),
        'title': row.title, 'status': row.status, 'target_at': _iso(row.target_at),
        'is_overdue': bool(row.target_at and row.target_at < now),
        'owner_name': row.owner_name,
    } for row in decisions if _decision_is_open(row)]

    exceptions = [{
        'asset_id': row.asset_id, 'asset_name': row.asset_name,
        'case_id': row.case_id, 'case_name': row.case_name,
        'stage_name': row.stage_name, 'reason': row.stage_reason,
        'decision_ref': _decision_ref(row.decision_number) if row.decision_number else None,
    } for row in sitreps_db_exception_assets(case_ids, _DRAFT_MAX_EXCEPTIONS,
                                             war_room_id=war_room_id)]

    next_actions = [{
        'type': 'task', 'task_id': row.task_id, 'title': row.title,
        'due_at': _iso(row.due_at), 'assignee_name': row.assignee_name,
    } for row in sitreps_db_open_tasks(war_room_id, _DRAFT_MAX_TASKS)]
    next_actions.extend({
        'type': 'decision', 'decision_id': row.decision_id,
        'ref': _decision_ref(row.number), 'title': row.title,
        'due_at': _iso(row.target_at),
    } for row in decisions
        if _decision_is_open(row) and row.target_at and now <= row.target_at <= soon)

    return {
        'changes': _collect_changes(war_room_id, case_ids, since, decisions),
        'cases': cases,
        'decisions': open_decisions,
        'exceptions': exceptions,
        'next_actions': next_actions,
    }


def _iso_to_display(value):
    if not value:
        return '—'
    return _fmt_dt(datetime.datetime.fromisoformat(value))


def _render_change(change):
    e = sitrep_md_escape
    at = _iso_to_display(change.get('at'))
    kind = change['type']
    if kind == 'stage':
        line = (f'- {at} — **{e(change["asset_name"])}** ({e(change["case_name"])}): '
                f'{e(change["from_stage_name"]) or "no stage"} → '
                f'{e(change["to_stage_name"]) or "no stage"}')
        if change.get('reason'):
            line += f' — {e(change["reason"])}'
        return line
    if kind == 'case_attached':
        return f'- {at} — Case #{change["case_id"]} {e(change["case_name"])} attached'
    verb = kind.replace('decision_', '')
    return f'- {at} — Decision **{change["ref"]}** {verb}: {e(change["title"])}'


def sitrep_auto_draft_render(war_room_name, since, generated_at, sections):
    """Pure markdown rendering of the auto-draft sections."""
    e = sitrep_md_escape
    cases = sections['cases']
    decisions = sections['decisions']
    assets = sum(c['assets_total'] for c in cases)
    done = sum(c['assets_done'] for c in cases)
    exceptions_count = sum(c['assets_exception'] for c in cases)
    overdue = sum(1 for d in decisions if d['is_overdue'])
    open_tasks = sum(1 for a in sections['next_actions'] if a['type'] == 'task')

    lines = [
        f'# SitRep — {e(war_room_name)}',
        '',
        f'_Generated {_fmt_dt(generated_at)} UTC — changes since {_fmt_dt(since)} UTC._',
        '',
        '## Summary',
        '',
        f'{len(cases)} case(s) · {assets} asset(s) · {done} done · {exceptions_count} exception(s) · '
        f'{len(decisions)} open decision(s) ({overdue} overdue) · {open_tasks} open war room task(s)',
        '',
        '## Changes since last SitRep',
        '',
    ]
    lines.extend(_render_change(c) for c in sections['changes'])
    if not sections['changes']:
        lines.append('_No changes._')

    lines += ['', '## Per-case status', '']
    if cases:
        lines.append('| Case | Customer | State | Assets | Done | Exceptions | Open tasks |')
        lines.append('|---|---|---|---|---|---|---|')
        for c in cases:
            lines.append(
                f'| #{c["case_id"]} {e(c["case_name"])} | {e(c["customer_name"])} | '
                f'{e(c["state_name"])} | {c["assets_total"]} | {c["assets_done"]} | '
                f'{c["assets_exception"]} | {c["tasks_open"]} |'
            )
    else:
        lines.append('_No readable attached case._')

    lines += ['', '## Decisions', '']
    for d in decisions:
        line = f'- **{d["ref"]}** {e(d["title"])} — {d["status"]}'
        if d['target_at']:
            line += f' · target {_iso_to_display(d["target_at"])} UTC'
        if d['is_overdue']:
            line += ' · **overdue**'
        if d.get('owner_name'):
            line += f' · owner {e(d["owner_name"])}'
        lines.append(line)
    if not decisions:
        lines.append('_No open decision._')

    lines += ['', '## Exceptions', '']
    for x in sections['exceptions']:
        line = (f'- **{e(x["asset_name"])}** ({e(x["case_name"])}) — '
                f'{e(x["stage_name"])}: {e(x["reason"]) or "no reason given"}')
        if x.get('decision_ref'):
            line += f' ({x["decision_ref"]})'
        lines.append(line)
    if not sections['exceptions']:
        lines.append('_No exception._')

    lines += ['', '## Next actions', '']
    for a in sections['next_actions']:
        if a['type'] == 'task':
            line = f'- [ ] {e(a["title"])}'
            if a.get('due_at'):
                line += f' — due {_iso_to_display(a["due_at"])} UTC'
            if a.get('assignee_name'):
                line += f' · {e(a["assignee_name"])}'
        else:
            line = (f'- [ ] Decision **{a["ref"]}** {e(a["title"])} — '
                    f'due {_iso_to_display(a["due_at"])} UTC')
        lines.append(line)
    if not sections['next_actions']:
        lines.append('_No pending action._')
    lines.append('')
    return '\n'.join(lines)


def sitrep_auto_draft(war_room_id, readable_case_ids):
    """Build a SitRep draft (not persisted) from the readable cases only."""
    war_room = war_room_get(war_room_id)
    now = datetime.datetime.utcnow()
    since = sitreps_db_last_published_at(war_room_id) or war_room.created_at
    sections = sitrep_auto_draft_sections(war_room_id, readable_case_ids, since, now)
    return {
        'title': f'SitRep — {war_room.name} — {_fmt_dt(now)} UTC'[:512],
        'body_md': sitrep_auto_draft_render(war_room.name, since, now, sections),
        'since': _iso(since),
        'generated_at': now.isoformat(),
        'sections': sections,
    }
