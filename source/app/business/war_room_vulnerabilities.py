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

"""Vulnerabilities tracked by a war room.

A war room opened on a fresh CVE tracks it before any asset of its cases
is known to be affected; the findings recorded later on case assets show
up next to it in the scope matrix. Tracking is per room and independent
of the findings: untracking never touches them.
"""

from app.business.vulnerabilities import vulnerabilities_get_or_create
from app.business.vulnerabilities import vulnerabilities_isoformat
from app.business.vulnerabilities import vulnerabilities_parse_id
from app.business.vulnerabilities import vulnerabilities_parse_query_bool
from app.business.vulnerabilities import vulnerabilities_parse_text
from app.business.vulnerabilities import vulnerabilities_serialize_short
from app.business.vulnerabilities_cve_sync import vulnerabilities_cve_fill_quick_add
from app.business.vulnerability_findings import vulnerability_findings_matrix_case_totals
from app.business.vulnerability_findings import vulnerability_findings_matrix_rows
from app.business.vulnerability_findings import vulnerability_findings_summary
from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_matrix_case_totals
from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_matrix_page
from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_rollback
from app.datamgmt.vulnerabilities.vulnerabilities_db import tracked_db_add
from app.datamgmt.vulnerabilities.vulnerabilities_db import tracked_db_counts
from app.datamgmt.vulnerabilities.vulnerabilities_db import tracked_db_delete
from app.datamgmt.vulnerabilities.vulnerabilities_db import tracked_db_get
from app.datamgmt.vulnerabilities.vulnerabilities_db import tracked_db_list
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_commit
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_get
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_get_many
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.vulnerabilities import WarRoomVulnerability


_NOTE_MAX_LENGTH = 2000
_SEARCH_MAX_LENGTH = 256
MATRIX_DEFAULT_PER_PAGE = 50
MATRIX_MAX_PER_PAGE = 200


def _tracking(tracking, added_by_name) -> dict:
    return {
        'note': tracking.note,
        'added_at': vulnerabilities_isoformat(tracking.added_at),
        'added_by_id': tracking.added_by_id,
        'added_by_name': added_by_name,
    }


def _serialize(tracking, vulnerability, added_by_name) -> dict:
    return {'vulnerability': vulnerabilities_serialize_short(vulnerability), **_tracking(tracking, added_by_name)}


def _hook_data(war_room_id, entry) -> dict:
    return {'war_room_id': war_room_id, 'identifier': entry['vulnerability']['identifier'], **entry}


def _parse_note(body):
    return vulnerabilities_parse_text(body.get('note'), 'note', _NOTE_MAX_LENGTH)


def _resolve(body, user_id):
    vulnerability_id = vulnerabilities_parse_id(body.get('vulnerability_id'), 'vulnerability_id')
    if vulnerability_id is not None:
        vulnerability = vulnerabilities_db_get(vulnerability_id)
        if vulnerability is None:
            raise BusinessProcessingError('Unknown vulnerability_id')
        return vulnerability
    if body.get('identifier'):
        # Same quick add as a finding: an unknown public identifier gets a
        # minimal catalogue entry, completed from cve.org when enabled.
        vulnerability = vulnerabilities_get_or_create(body['identifier'], body.get('title'), user_id)
        vulnerabilities_cve_fill_quick_add(vulnerability, user_id)
        return vulnerability
    raise BusinessProcessingError('vulnerability_id or identifier is required')


def _get(war_room_id, vulnerability_id):
    tracking = tracked_db_get(war_room_id, vulnerability_id)
    if tracking is None:
        raise ObjectNotFoundError()
    return tracking


def _one(war_room_id, vulnerability_id) -> dict:
    for tracking, vulnerability, added_by_name in tracked_db_list(war_room_id):
        if vulnerability.vulnerability_id == vulnerability_id:
            return _serialize(tracking, vulnerability, added_by_name)
    raise ObjectNotFoundError()


def war_room_vulnerabilities_list(war_room_id) -> list:
    return [_serialize(*row) for row in tracked_db_list(war_room_id)]


def _parse_positive_int(value, field, default):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a positive integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise BusinessProcessingError(f'{field} must be a positive integer')
    if parsed <= 0:
        raise BusinessProcessingError(f'{field} must be a positive integer')
    return parsed


def _matrix_filters(args, case_ids, hide_private_content):
    """`(filters, scope_case_ids, page, per_page)` from the query mapping."""
    page = _parse_positive_int(args.get('page'), 'page', 1)
    per_page = _parse_positive_int(args.get('per_page'), 'per_page', MATRIX_DEFAULT_PER_PAGE)
    if per_page > MATRIX_MAX_PER_PAGE:
        raise BusinessProcessingError(f'per_page must be at most {MATRIX_MAX_PER_PAGE}')
    search = args.get('search')
    if search is not None and not isinstance(search, str):
        raise BusinessProcessingError('search must be a string')
    search = vulnerabilities_parse_text(search, 'search', _SEARCH_MAX_LENGTH) or None
    case_id = _parse_positive_int(args.get('case_id'), 'case_id', None)
    if case_id is not None and case_id not in case_ids:
        raise BusinessProcessingError('case_id must be an attached case you can read')
    filters = {
        'search': search,
        'tracked_only': vulnerabilities_parse_query_bool(args.get('tracked'), 'tracked') is True,
        # One case: the entries found in it (tracked ones included).
        'observed_only': case_id is not None,
        'private_content_hidden': hide_private_content,
    }
    return filters, [case_id] if case_id is not None else list(case_ids), page, per_page


def war_room_vulnerabilities_matrix(war_room_id, case_ids, args=None, hide_private_content=False) -> dict:
    """One page of the scope matrix: the entries found in `case_ids` (the
    attached cases the caller can read) plus those the room tracks, worst
    first. `args` (query mapping): `page`, `per_page`, `search`, `case_id`
    (one of `case_ids`), `tracked`. `case_totals` are the per-case column
    totals over every filtered entry; `summary` covers `case_ids`, unfiltered.
    With `hide_private_content`, private entries only match on their identifier."""
    filters, scope, page, per_page = _matrix_filters(args or {}, case_ids, hide_private_content)
    vulnerability_ids, total = findings_db_matrix_page(scope, war_room_id, filters, page, per_page)
    vulnerabilities = vulnerabilities_db_get_many(vulnerability_ids)
    tracked = {vulnerability.vulnerability_id: _tracking(tracking, added_by_name)
               for tracking, vulnerability, added_by_name in tracked_db_list(war_room_id, vulnerability_ids)}
    rows = vulnerability_findings_matrix_rows(
        scope, [vulnerabilities[vid] for vid in vulnerability_ids if vid in vulnerabilities], tracked)

    summary = vulnerability_findings_summary(case_ids)
    summary['tracked'], summary['tracked_unobserved'] = tracked_db_counts(war_room_id, case_ids)
    return {
        'vulnerabilities': rows,
        'summary': summary,
        'case_totals': vulnerability_findings_matrix_case_totals(
            findings_db_matrix_case_totals(scope, war_room_id, filters)),
        'total': total,
        'page': page,
        'per_page': per_page,
    }


def war_room_vulnerabilities_track(war_room_id, body, user_id):
    """Track an entry (by `vulnerability_id`, or `identifier` with quick
    add). Idempotent: `(entry, created)`; an already tracked entry only
    gets its note updated when one is given."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    note = _parse_note(body)
    vulnerability = _resolve(body, user_id)
    tracking = tracked_db_get(war_room_id, vulnerability.vulnerability_id)
    created = tracking is None
    if created:
        tracking = WarRoomVulnerability(war_room_id=war_room_id, vulnerability_id=vulnerability.vulnerability_id,
                                        note=note, added_by_id=user_id)
        tracked_db_add(tracking)
    elif note is not None:
        tracking.note = note
    if not vulnerabilities_db_commit():
        # Tracked concurrently: the other request won, same outcome.
        created = False
    entry = _one(war_room_id, vulnerability.vulnerability_id)
    if created:
        track_activity(f'tracked vulnerability #{vulnerability.vulnerability_id}', war_room_id=war_room_id)
        call_modules_hook('on_postload_war_room_vulnerability_track', _hook_data(war_room_id, entry))
    elif note is not None:
        call_modules_hook('on_postload_war_room_vulnerability_update', _hook_data(war_room_id, entry))
    return entry, created


def war_room_vulnerabilities_update(war_room_id, vulnerability_id, body) -> dict:
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    tracking = _get(war_room_id, vulnerability_id)
    if 'note' in body:
        tracking.note = _parse_note(body)
    if not vulnerabilities_db_commit():
        findings_db_rollback()
        raise BusinessProcessingError('Unable to update the tracked vulnerability')
    entry = _one(war_room_id, vulnerability_id)
    call_modules_hook('on_postload_war_room_vulnerability_update', _hook_data(war_room_id, entry))
    return entry


def war_room_vulnerabilities_untrack(war_room_id, vulnerability_id) -> None:
    """Stop tracking; the findings on case assets are kept."""
    tracking = _get(war_room_id, vulnerability_id)
    vulnerability = vulnerabilities_db_get(vulnerability_id)
    tracked_db_delete(tracking)
    vulnerabilities_db_commit()
    # War-room activity is read without the vulnerability permission.
    track_activity(f'stopped tracking vulnerability #{vulnerability_id}', war_room_id=war_room_id)
    call_modules_hook('on_postload_war_room_vulnerability_untrack', {
        'war_room_id': war_room_id,
        'vulnerability_id': vulnerability_id,
        'identifier': vulnerability.identifier if vulnerability is not None else None,
    })
