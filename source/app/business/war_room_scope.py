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

"""War-room scope: assets and IOCs across the attached cases.

Authorization is decided by the blueprint, which hands this module the
explicit sets of case ids the caller may read (`readable_case_ids`) and
write (`writable_case_ids`, attached + full access). This module never
widens them: any target outside those sets yields a `denied` result
row, and objects of cases outside `readable_case_ids` are reported as
not found so their existence does not leak.

Objects are created in cases through the regular case business
functions (`assets_create`, `iocs_create`) so module hooks, object
history, webhooks and activity logging keep firing exactly as when the
object is created from the case itself.
"""

import csv
import datetime
import io
import ipaddress
import json
import re
import uuid

from marshmallow import ValidationError

from app.business.asset_flags import asset_flags_clear_for_asset
from app.business.asset_flags import asset_flags_is_unchanged
from app.business.asset_flags import asset_flags_set_for_asset
from app.business.assets import assets_create
from app.business.iocs import iocs_create
from app.business.vulnerabilities import vulnerabilities_normalize_identifier
from app.datamgmt.vulnerabilities.vulnerabilities_db import findings_db_asset_tags
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_analysis_status_exists
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_asset_type_exists
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_asset_sightings
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_asset_flags
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_assets
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_assets_breakdown
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_assets_by_ids
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_attached_case_ids
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_cases
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_chat_message_in_room
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_decision_in_room
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_find_asset
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_flag_exists
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_flag_totals
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_find_ioc
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_ioc_type_get
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_ioc_case_counts
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_ioc_keys_count
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_ioc_keys_page
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_iocs
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_iocs_by_ids
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_rollback
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_add
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_count
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_delete
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_get
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_list
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_staged_save
from app.datamgmt.war_rooms.war_room_scope_db import war_room_scope_db_tlp_exists
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.logger import logger
from app.models.assets import CompromiseStatus
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.war_rooms import WarRoomStagedObject
from app.schema.marshables import CaseAssetsSchema
from app.schema.marshables import IocSchemaForAPIV2


WAR_ROOM_SCOPE_LIST_LIMIT = 5000
WAR_ROOM_SCOPE_MAX_SOURCES = 200
WAR_ROOM_SCOPE_MAX_TARGETS = 50
WAR_ROOM_SCOPE_MAX_BULK_FLAG = 500
WAR_ROOM_SCOPE_MAX_STAGED = 500
WAR_ROOM_SCOPE_DEFAULT_PER_PAGE = 100
WAR_ROOM_SCOPE_MAX_PER_PAGE = 500
WAR_ROOM_SCOPE_EXPORT_FORMATS = ('txt', 'csv', 'stix')

_EXPORT_LIMIT = 50000
_STAGED_OBJECT_TYPES = ('asset', 'ioc')
_ASSET_SORTS = ('name', 'case')
_IOC_SORTS = ('value', 'spread')
_MAX_SIGHTING_CASES = 50
# Rows of one IOC page: per_page indicators, each seen in up to every
# attached case.
_MAX_IOC_PAGE_ROWS = 50000
_MAX_SEARCH = 256
_MAX_ASSET_NAME = 512
_MAX_TEXT = 20000
_MAX_SHORT_TEXT = 1024
_MAX_ASSET_TAGS = 2048
_MAX_IOC_TAGS = 512
_MAX_IOC_VALUE = 20000
_MAX_REASON = 4000
_MAX_NOTE = 4000

_DENIED_CASE_MESSAGE = 'Case not attached to this war room or access denied'
_SOURCE_NOT_FOUND_MESSAGE = 'Source object not found'
_UNEXPECTED_ERROR_MESSAGE = 'Unexpected error, the object was not created'

_CSV_DANGEROUS_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

# Most restrictive first. Unknown / missing TLP ranks lowest.
_TLP_RANK = {'red': 4, 'amber+strict': 3, 'amber': 2, 'green': 1, 'clear': 0, 'white': 0}

# Fixed STIX 2.1 TLP marking-definition ids (OASIS TLP 1.0 markings).
_STIX_TLP_MARKINGS = {
    'clear': 'marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9',
    'white': 'marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9',
    'green': 'marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da',
    'amber': 'marking-definition--f88d31f6-486f-44da-b317-01333bde0b82',
    'red': 'marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed',
}
_STIX_NAMESPACE = uuid.UUID('6b1c51b2-7f4e-4f0c-9c8b-3a2f0d1e5a77')
_STIX_HASH_KEYS = {'md5': 'MD5', 'sha1': "'SHA-1'", 'sha224': "'SHA-224'", 'sha256': "'SHA-256'",
                   'sha384': "'SHA-384'", 'sha512': "'SHA-512'"}
_STIX_SIMPLE_TYPES = {
    'domain': 'domain-name:value',
    'hostname': 'domain-name:value',
    'url': 'url:value',
    'uri': 'url:value',
    'link': 'url:value',
    'email': 'email-addr:value',
    'email-src': 'email-addr:value',
    'email-dst': 'email-addr:value',
    'filename': 'file:name',
    'mac-address': 'mac-addr:value',
    'regkey': 'windows-registry-key:key',
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_int_id(value, field, allow_none=False):
    if value is None and allow_none:
        return None
    if not _is_int(value) or value <= 0:
        raise BusinessProcessingError(f'{field} must be a positive integer')
    return value


def _validate_text(value, field, max_len, required=False):
    if value is None:
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    if not value:
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if len(value) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return value


def _validate_tags(value, field, max_len):
    """Accept a comma-separated string or a list of strings."""
    if value is None:
        return None
    if isinstance(value, list):
        parts = []
        for tag in value:
            if not isinstance(tag, str):
                raise BusinessProcessingError(f'{field} must only contain strings')
            parts.append(tag)
    elif isinstance(value, str):
        parts = value.split(',')
    else:
        raise BusinessProcessingError(f'{field} must be a string or a list of strings')
    tags = []
    for tag in parts:
        tag = tag.strip()
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        return None
    joined = ','.join(tags)
    if len(joined) > max_len:
        raise BusinessProcessingError(f'{field} must be at most {max_len} characters')
    return joined


def _validate_id_list(value, field, max_items, min_items=1):
    """List of positive ints, de-duplicated in order."""
    if not isinstance(value, list):
        raise BusinessProcessingError(f'{field} must be a list of integers')
    ids = []
    for item in value:
        if not _is_int(item) or item <= 0:
            raise BusinessProcessingError(f'{field} must be a list of integers')
        if item not in ids:
            ids.append(item)
    if len(ids) < min_items:
        raise BusinessProcessingError(f'{field} must contain at least {min_items} item(s)')
    if len(ids) > max_items:
        raise BusinessProcessingError(f'{field} must contain at most {max_items} items')
    return ids


def _validate_flag_id(flag_id, field='flag_id'):
    _validate_int_id(flag_id, field)
    if not war_room_scope_db_flag_exists(flag_id):
        raise BusinessProcessingError('Flag not found')
    return flag_id


def _validate_flag_ids(flag_ids):
    if flag_ids is None:
        return []
    ids = _validate_id_list(flag_ids, 'flag_ids', WAR_ROOM_SCOPE_MAX_TARGETS, min_items=0)
    for flag_id in ids:
        _validate_flag_id(flag_id)
    return ids


def _validate_asset_payload(payload):
    """Allow-listed, type-checked asset fields. Unknown keys are dropped."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('asset must be an object')

    asset_type_id = _validate_int_id(payload.get('asset_type_id'), 'asset_type_id')
    if not war_room_scope_db_asset_type_exists(asset_type_id):
        raise BusinessProcessingError('Invalid asset type ID')

    clean = {
        'asset_name': _validate_text(payload.get('asset_name'), 'asset_name', _MAX_ASSET_NAME, required=True),
        'asset_type_id': asset_type_id,
    }
    for field, max_len in (('asset_description', _MAX_TEXT),
                           ('asset_ip', _MAX_SHORT_TEXT),
                           ('asset_domain', _MAX_SHORT_TEXT)):
        value = _validate_text(payload.get(field), field, max_len)
        if value is not None:
            clean[field] = value

    tags = _validate_tags(payload.get('asset_tags'), 'asset_tags', _MAX_ASSET_TAGS)
    if tags is not None:
        clean['asset_tags'] = tags

    compromise = payload.get('asset_compromise_status_id')
    if compromise is not None:
        if not _is_int(compromise) or compromise not in {status.value for status in CompromiseStatus}:
            raise BusinessProcessingError('Invalid asset_compromise_status_id')
        clean['asset_compromise_status_id'] = compromise

    analysis_status_id = payload.get('analysis_status_id')
    if analysis_status_id is not None:
        _validate_int_id(analysis_status_id, 'analysis_status_id')
        if not war_room_scope_db_analysis_status_exists(analysis_status_id):
            raise BusinessProcessingError('Invalid analysis status ID')
        clean['analysis_status_id'] = analysis_status_id

    return clean


def _ioc_value_matches_type(ioc_type, value):
    regex = getattr(ioc_type, 'type_validation_regex', None)
    if not regex:
        return True
    try:
        return re.fullmatch(regex, value, re.IGNORECASE) is not None
    except re.error:
        # A broken admin-defined regex must not block every IOC of that type;
        # the case-side schema would raise too, so creation still reports it.
        return True


def _validate_ioc_payload(payload):
    """Allow-listed, type-checked IOC fields. Unknown keys are dropped."""
    if not isinstance(payload, dict):
        raise BusinessProcessingError('ioc must be an object')

    ioc_value = _validate_text(payload.get('ioc_value'), 'ioc_value', _MAX_IOC_VALUE, required=True)
    ioc_type_id = _validate_int_id(payload.get('ioc_type_id'), 'ioc_type_id')
    ioc_type = war_room_scope_db_ioc_type_get(ioc_type_id)
    if ioc_type is None:
        raise BusinessProcessingError('Invalid IOC type ID')
    if not _ioc_value_matches_type(ioc_type, ioc_value):
        expected = ioc_type.type_validation_expect or ioc_type.type_validation_regex
        raise BusinessProcessingError(f"The input doesn't match the expected format (expected: {expected})")

    clean = {'ioc_value': ioc_value, 'ioc_type_id': ioc_type_id}

    tlp_id = payload.get('ioc_tlp_id')
    if tlp_id is not None:
        _validate_int_id(tlp_id, 'ioc_tlp_id')
        if not war_room_scope_db_tlp_exists(tlp_id):
            raise BusinessProcessingError('Invalid TLP ID')
        clean['ioc_tlp_id'] = tlp_id

    description = _validate_text(payload.get('ioc_description'), 'ioc_description', _MAX_TEXT)
    if description is not None:
        clean['ioc_description'] = description

    tags = _validate_tags(payload.get('ioc_tags'), 'ioc_tags', _MAX_IOC_TAGS)
    if tags is not None:
        clean['ioc_tags'] = tags

    return clean


def _validate_payload_for_type(object_type, payload):
    if object_type == 'asset':
        return _validate_asset_payload(payload)
    return _validate_ioc_payload(payload)


# ---------------------------------------------------------------------------
# Case lookups shared with the blueprint
# ---------------------------------------------------------------------------

def war_room_scope_attached_case_ids(war_room_id):
    """Ids of the cases attached to the war room, in attachment order."""
    return war_room_scope_db_attached_case_ids(war_room_id)


def _cases_info(case_ids):
    return [
        {
            'case_id': row.case_id,
            'case_name': row.case_name,
            'customer_id': row.customer_id,
            'customer_name': row.customer_name,
            'accessible': True,
        }
        for row in war_room_scope_db_cases(case_ids)
    ]


def _customers_summary(case_ids):
    customers = {}
    for row in war_room_scope_db_cases(case_ids):
        if row.customer_id is not None and row.customer_id not in customers:
            customers[row.customer_id] = {'customer_id': row.customer_id, 'customer_name': row.customer_name}
    return list(customers.values())


def _restrict_case_ids(readable_case_ids, case_id):
    ordered = list(readable_case_ids)
    if case_id is None:
        return ordered
    return [cid for cid in ordered if cid == case_id]


def _parse_optional_int_arg(raw, field):
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise BusinessProcessingError(f'{field} must be an integer')
    if value <= 0:
        raise BusinessProcessingError(f'{field} must be a positive integer')
    return value


def _parse_search(raw):
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return raw[:_MAX_SEARCH]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

_SEVERITY_BY_RANK = {5: 'critical', 4: 'high', 3: 'medium', 2: 'low', 1: 'none', 0: 'unknown'}
_VULNERABLE_FILTERS = {'1': 'open', 'true': 'open', 'open': 'open', 'exploited': 'exploited',
                       '0': 'none', 'false': 'none', 'none': 'none'}


def _parse_vulnerable(raw):
    if raw is None or raw == '':
        return None
    value = _VULNERABLE_FILTERS.get(str(raw).strip().lower())
    if value is None:
        raise BusinessProcessingError('vulnerable must be one of: open, exploited, none')
    return value


def _parse_vulnerability(raw):
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    return vulnerabilities_normalize_identifier(raw, field='vulnerability')


def _vulnerability_tags(asset_ids):
    """{asset_id: [tag]}, one tag per finding, highest severity first."""
    tags = {}
    for row in findings_db_asset_tags(asset_ids):
        tags.setdefault(row.asset_id, []).append({
            'finding_id': row.finding_id,
            'vulnerability_id': row.vulnerability_id,
            'identifier': row.identifier,
            'severity': row.severity,
            'kev': bool(row.kev),
            'remediation_status': row.remediation_status,
            'exploitation_status': row.exploitation_status,
        })
    return tags


def _serialize_scope_asset(row, vulnerabilities=None, flags=None):
    name = row.asset_name or ''
    return {
        'asset_id': row.asset_id,
        'asset_uuid': str(row.asset_uuid) if row.asset_uuid else None,
        'asset_name': row.asset_name,
        'asset_type_id': row.asset_type_id,
        'asset_type_name': row.asset_type_name,
        'asset_ip': row.asset_ip,
        'asset_domain': row.asset_domain,
        'asset_description': row.asset_description,
        'asset_tags': row.asset_tags,
        'asset_compromise_status_id': row.asset_compromise_status_id,
        'analysis_status_id': row.analysis_status_id,
        'analysis_status_name': row.analysis_status_name,
        'flags': flags or [],
        'case_id': row.case_id,
        'case_name': row.case_name,
        'customer_id': row.customer_id,
        'customer_name': row.customer_name,
        'ioc_count': int(row.ioc_count or 0),
        'vuln_open_count': int(row.vuln_open_count or 0),
        'vuln_total_count': int(row.vuln_total_count or 0),
        'vuln_exploited_count': int(row.vuln_exploited_count or 0),
        'vuln_exploited_open_count': int(row.vuln_exploited_open_count or 0),
        'vuln_max_severity': _SEVERITY_BY_RANK.get(row.vuln_max_rank),
        'vulnerabilities': vulnerabilities or [],
        'date_update': row.date_update.isoformat() if row.date_update else None,
        'group_key': f'{row.asset_type_id}:{name.lower()}',
    }


def _ioc_group_key(value):
    """Key under which the same indicator is recognised across cases.

    Analysts type IOCs differently from one case to the next (`ip-src`
    vs `ip-dst`, `Evil.COM` vs `evil.com`, stray whitespace), so the
    value is trimmed and lower-cased and the type is ignored."""
    return f'ioc:{(value or "").strip().lower()}'


def _serialize_scope_ioc(row):
    return {
        'ioc_id': row.ioc_id,
        'ioc_value': row.ioc_value,
        'ioc_type_id': row.ioc_type_id,
        'ioc_type_name': row.ioc_type_name,
        'ioc_tlp_id': row.ioc_tlp_id,
        'tlp_name': row.tlp_name,
        'ioc_description': row.ioc_description,
        'ioc_tags': row.ioc_tags,
        'case_id': row.case_id,
        'case_name': row.case_name,
        'customer_id': row.customer_id,
        'customer_name': row.customer_name,
        'group_key': _ioc_group_key(row.ioc_value),
    }


def _parse_pagination(page, per_page):
    """`(page, per_page)` or None when `page` is absent (legacy, unpaginated
    listing capped at WAR_ROOM_SCOPE_LIST_LIMIT)."""
    page = _parse_optional_int_arg(page, 'page')
    if page is None:
        return None
    per_page = _parse_optional_int_arg(per_page, 'per_page') or WAR_ROOM_SCOPE_DEFAULT_PER_PAGE
    if per_page > WAR_ROOM_SCOPE_MAX_PER_PAGE:
        raise BusinessProcessingError(f'per_page must be at most {WAR_ROOM_SCOPE_MAX_PER_PAGE}')
    return page, per_page


def _parse_sort(raw, allowed):
    if raw is None or raw == '':
        return allowed[0]
    value = str(raw).strip().lower()
    if value not in allowed:
        raise BusinessProcessingError(f'sort must be one of: {", ".join(allowed)}')
    return value


def _assets_aggregates(breakdown_rows, flag_rows):
    """`(total, case_totals, flag_totals)` from the per-case and per-flag
    rows. `flag_totals` has one `flag_id: None` entry for the assets
    without any flag; an asset carrying several flags counts under each."""
    case_totals = []
    total = 0
    unflagged = 0
    for row in breakdown_rows:
        assets = int(row.assets or 0)
        total += assets
        unflagged += int(row.unflagged or 0)
        case_totals.append({'case_id': row.case_id, 'assets': assets, 'done': int(row.done or 0),
                            'vuln_open': int(row.vuln_open or 0),
                            'vuln_exploited_open': int(row.vuln_exploited_open or 0)})
    case_totals.sort(key=lambda entry: entry['case_id'])
    flag_totals = [{'flag_id': row.flag_id, 'assets': int(row.assets or 0)} for row in flag_rows]
    flag_totals.sort(key=lambda entry: entry['flag_id'])
    if unflagged:
        flag_totals.insert(0, {'flag_id': None, 'assets': unflagged})
    return total, case_totals, flag_totals


def _asset_flags(asset_ids):
    """{asset_id: [flag entries]} for the scope listing. One query."""
    flags = {}
    for row in war_room_scope_db_asset_flags(asset_ids):
        flags.setdefault(row.asset_id, []).append({
            'flag_id': row.flag_id,
            'reason': row.reason,
            'decision_id': row.decision_id,
            'set_at': row.set_at.isoformat() if row.set_at else None,
        })
    return flags


def _asset_sightings(readable_case_ids, rows):
    """{(asset_type_id, lower name): [case_id]}: the readable cases holding
    an asset of the same type and name as one of `rows`. One query."""
    names = {(row.asset_name or '').lower() for row in rows}
    sightings = {}
    for row in war_room_scope_db_asset_sightings(readable_case_ids, names):
        sightings.setdefault((row.asset_type_id, row.name_key), set()).add(row.case_id)
    return {key: sorted(case_ids) for key, case_ids in sightings.items()}


_ASSET_VULNERABILITY_FIELDS = ('vuln_open_count', 'vuln_total_count', 'vuln_exploited_count',
                               'vuln_exploited_open_count', 'vuln_max_severity', 'vulnerabilities')
_CASE_TOTAL_VULNERABILITY_FIELDS = ('vuln_open', 'vuln_exploited_open')


def _without_vulnerabilities(result):
    """Drop every vulnerability field from an assets listing."""
    for item in result['data']:
        for field in _ASSET_VULNERABILITY_FIELDS:
            item.pop(field, None)
    for entry in result.get('case_totals') or ():
        for field in _CASE_TOTAL_VULNERABILITY_FIELDS:
            entry.pop(field, None)
    return result


def war_room_scope_list_assets(readable_case_ids, search=None, flag=None, case_id=None, compromised=None,
                               vulnerable=None, vulnerability=None, page=None, per_page=None, sort=None,
                               include_vulnerabilities=True, without_flag=None):
    """Assets of the readable attached cases.

    Query-string inputs are passed raw: `flag` is a flag id (assets
    carrying it) or `'none'` (assets without any flag), `without_flag` a
    flag id (assets not carrying it),
    `case_id` an int, `compromised` `'1'`/`'true'`, `vulnerable`
    `'open'`/`'exploited'`/`'none'`, `vulnerability` an identifier
    (`CVE-2024-3400`, an alias, an `IRIS-VULN-…` entry).

    Without `page`, at most WAR_ROOM_SCOPE_LIST_LIMIT rows are returned
    (`truncated` tells when more exist). With `page` (1-based) and
    `per_page`, one page is returned along with `total`, the per-case and
    per-flag totals over every matching asset, and on each asset the
    other readable cases holding the same asset (`sighting_case_ids`,
    capped, and `sighting_count`).

    Without `include_vulnerabilities` (the caller lacks the vulnerability
    read permission) the vulnerability fields are left out and the
    vulnerability filters refused.
    """
    if not include_vulnerabilities:
        if (vulnerable or '') != '' or (vulnerability or '') != '':
            raise BusinessProcessingError('Filtering on vulnerabilities needs the vulnerabilities_read permission')
        return _without_vulnerabilities(_list_assets(
            readable_case_ids, search, flag, without_flag, case_id, compromised, None, None, page, per_page, sort,
            False))
    return _list_assets(readable_case_ids, search, flag, without_flag, case_id, compromised, vulnerable,
                        vulnerability, page, per_page, sort, True)


def _list_assets(readable_case_ids, search, flag, without_flag, case_id, compromised, vulnerable, vulnerability,
                 page, per_page, sort, with_tags):
    search = _parse_search(search)
    case_filter = _parse_optional_int_arg(case_id, 'case_id')
    flag_none = False
    flag_id = None
    if flag is not None and flag != '':
        if isinstance(flag, str) and flag.lower() == 'none':
            flag_none = True
        else:
            flag_id = _parse_optional_int_arg(flag, 'flag_id')
    without_flag_id = _parse_optional_int_arg(without_flag, 'without_flag_id')
    only_compromised = str(compromised).lower() in ('1', 'true') if compromised is not None else False
    vulnerable_filter = _parse_vulnerable(vulnerable)
    vulnerability_filter = _parse_vulnerability(vulnerability)
    pagination = _parse_pagination(page, per_page)
    sort = _parse_sort(sort, _ASSET_SORTS)

    case_ids = _restrict_case_ids(readable_case_ids, case_filter)
    filters = {'search': search, 'flag_id': flag_id, 'flag_none': flag_none, 'without_flag_id': without_flag_id,
               'compromised': only_compromised, 'vulnerable': vulnerable_filter,
               'vulnerability': vulnerability_filter}

    if pagination is None:
        rows = war_room_scope_db_assets(case_ids, limit=WAR_ROOM_SCOPE_LIST_LIMIT, **filters)
        truncated = len(rows) > WAR_ROOM_SCOPE_LIST_LIMIT
        rows = rows[:WAR_ROOM_SCOPE_LIST_LIMIT]
        tags = _vulnerability_tags([row.asset_id for row in rows]) if with_tags else {}
        flags = _asset_flags([row.asset_id for row in rows])
        return {
            'data': [_serialize_scope_asset(row, tags.get(row.asset_id), flags.get(row.asset_id)) for row in rows],
            'truncated': truncated,
            'limit': WAR_ROOM_SCOPE_LIST_LIMIT,
            'cases': _cases_info(list(readable_case_ids)),
        }

    page, per_page = pagination
    rows = war_room_scope_db_assets(case_ids, limit=per_page, offset=(page - 1) * per_page, sort=sort, **filters)
    rows = rows[:per_page]
    total, case_totals, flag_totals = _assets_aggregates(war_room_scope_db_assets_breakdown(case_ids, **filters),
                                                         war_room_scope_db_flag_totals(case_ids, **filters))
    tags = _vulnerability_tags([row.asset_id for row in rows]) if with_tags else {}
    flags = _asset_flags([row.asset_id for row in rows])
    sightings = _asset_sightings(list(readable_case_ids), rows)
    data = []
    for row in rows:
        item = _serialize_scope_asset(row, tags.get(row.asset_id), flags.get(row.asset_id))
        others = [cid for cid in sightings.get((row.asset_type_id, (row.asset_name or '').lower()), ())
                  if cid != row.case_id]
        item['sighting_count'] = len(others)
        item['sighting_case_ids'] = others[:_MAX_SIGHTING_CASES]
        data.append(item)
    return {
        'data': data,
        'truncated': False,
        'limit': per_page,
        'cases': _cases_info(list(readable_case_ids)),
        'total': total,
        'page': page,
        'per_page': per_page,
        'sort': sort,
        'case_totals': case_totals,
        'flag_totals': flag_totals,
    }


def war_room_scope_list_iocs(readable_case_ids, search=None, case_id=None, page=None, per_page=None, sort=None):
    """IOCs of the readable attached cases.

    Without `page`, at most WAR_ROOM_SCOPE_LIST_LIMIT rows. With `page`,
    the pagination is over the distinct indicators (`group_key`, the
    trimmed lower-cased value): one page of indicators with every row of
    each, `total` the number of distinct indicators and `case_totals` the
    number of distinct indicators per case. `sort` is `value` or `spread`
    (indicators seen in the most cases first)."""
    search = _parse_search(search)
    case_filter = _parse_optional_int_arg(case_id, 'case_id')
    pagination = _parse_pagination(page, per_page)
    sort = _parse_sort(sort, _IOC_SORTS)
    case_ids = _restrict_case_ids(readable_case_ids, case_filter)

    if pagination is None:
        rows = war_room_scope_db_iocs(case_ids, search=search, limit=WAR_ROOM_SCOPE_LIST_LIMIT)
        truncated = len(rows) > WAR_ROOM_SCOPE_LIST_LIMIT
        return {
            'data': [_serialize_scope_ioc(row) for row in rows[:WAR_ROOM_SCOPE_LIST_LIMIT]],
            'truncated': truncated,
            'limit': WAR_ROOM_SCOPE_LIST_LIMIT,
            'cases': _cases_info(list(readable_case_ids)),
        }

    page, per_page = pagination
    key_rows = war_room_scope_db_ioc_keys_page(case_ids, search=search, offset=(page - 1) * per_page,
                                               limit=per_page, sort=sort)
    keys = [row.key for row in key_rows]
    rows = war_room_scope_db_iocs(case_ids, search=search, limit=_MAX_IOC_PAGE_ROWS, keys=keys) if keys else []
    truncated = len(rows) > _MAX_IOC_PAGE_ROWS
    rows = rows[:_MAX_IOC_PAGE_ROWS]
    rank = {key: index for index, key in enumerate(keys)}
    rows = sorted(rows, key=lambda row: rank.get((row.ioc_value or '').strip().lower(), len(rank)))
    return {
        'data': [_serialize_scope_ioc(row) for row in rows],
        'truncated': truncated,
        'limit': per_page,
        'cases': _cases_info(list(readable_case_ids)),
        'total': war_room_scope_db_ioc_keys_count(case_ids, search=search),
        'page': page,
        'per_page': per_page,
        'sort': sort,
        'case_totals': [{'case_id': row.case_id, 'iocs': int(row.iocs or 0)}
                        for row in sorted(war_room_scope_db_ioc_case_counts(case_ids, search=search),
                                          key=lambda row: row.case_id)],
    }


# ---------------------------------------------------------------------------
# Creation in cases
# ---------------------------------------------------------------------------

def _create_asset_in_case(user, case_id, clean):
    """Create through the case business function so hooks/history/activity fire."""
    try:
        asset = CaseAssetsSchema().load(dict(clean))
    except ValidationError as e:
        raise BusinessProcessingError(f'Data error: {e.messages}')
    return assets_create(user, case_id, asset, None)


def _create_ioc_in_case(case_id, clean):
    data = dict(clean)
    data['case_id'] = case_id
    try:
        ioc = IocSchemaForAPIV2().load(data)
    except ValidationError as e:
        raise BusinessProcessingError(f'Data error: {e.messages}')
    return iocs_create(ioc)


def _unexpected_error(context):
    logger.exception(f'War-room scope: unexpected error while {context}')
    try:
        war_room_scope_db_rollback()
    except Exception:
        logger.exception('War-room scope: rollback failed')


def _create_asset_row(user, case_id, clean):
    """One result row: created | exists | error. Never raises."""
    try:
        existing_id = war_room_scope_db_find_asset(case_id, clean['asset_name'], clean['asset_type_id'])
        if existing_id is not None:
            return {'status': 'exists', 'object_id': existing_id, 'asset': None}
        asset = _create_asset_in_case(user, case_id, clean)
        return {'status': 'created', 'object_id': asset.asset_id, 'asset': asset}
    except BusinessProcessingError as e:
        war_room_scope_db_rollback()
        return {'status': 'error', 'message': e.get_message()}
    except Exception:
        _unexpected_error(f'creating an asset in case {case_id}')
        return {'status': 'error', 'message': _UNEXPECTED_ERROR_MESSAGE}


def _create_ioc_row(case_id, clean):
    try:
        existing_id = war_room_scope_db_find_ioc(case_id, clean['ioc_value'])
        if existing_id is not None:
            return {'status': 'exists', 'object_id': existing_id}
        ioc = _create_ioc_in_case(case_id, clean)
        return {'status': 'created', 'object_id': ioc.ioc_id}
    except BusinessProcessingError as e:
        war_room_scope_db_rollback()
        return {'status': 'error', 'message': e.get_message()}
    except Exception:
        _unexpected_error(f'creating an IOC in case {case_id}')
        return {'status': 'error', 'message': _UNEXPECTED_ERROR_MESSAGE}


def _apply_flags_after_create(war_room_id, user_id, asset, flag_ids, flag_reason):
    """Returns None on success, an error message otherwise. Never raises."""
    asset_id = asset.asset_id
    for flag_id in flag_ids:
        try:
            asset = asset_flags_set_for_asset(asset, flag_id, flag_reason, None, user_id, war_room_id=war_room_id)
        except BusinessProcessingError as e:
            war_room_scope_db_rollback()
            return f'Created, but the flags were not all set: {e.get_message()}'
        except Exception:
            _unexpected_error(f'setting the flags of asset {asset_id}')
            return 'Created, but the flags were not all set'
    return None


def _create_object_in_cases(war_room_id, user, object_type, clean, target_case_ids, writable_case_ids,
                            flag_ids=None, flag_reason=None):
    writable = set(writable_case_ids)
    id_key = 'asset_id' if object_type == 'asset' else 'ioc_id'
    results = []
    created = 0
    for case_id in target_case_ids:
        if case_id not in writable:
            results.append({'case_id': case_id, 'status': 'denied', 'message': _DENIED_CASE_MESSAGE})
            continue
        if object_type == 'asset':
            outcome = _create_asset_row(user, case_id, clean)
        else:
            outcome = _create_ioc_row(case_id, clean)
        row = {'case_id': case_id, 'status': outcome['status']}
        if outcome.get('object_id') is not None:
            row[id_key] = outcome['object_id']
        if outcome.get('message'):
            row['message'] = outcome['message']
        if outcome['status'] == 'created':
            created += 1
            if object_type == 'asset' and flag_ids:
                message = _apply_flags_after_create(war_room_id, user.id, outcome['asset'], flag_ids, flag_reason)
                if message:
                    row['message'] = message
        results.append(row)

    if created:
        label = clean['asset_name'] if object_type == 'asset' else clean['ioc_value']
        track_activity(f'added {object_type} "{label}" to {created} case(s) from the war room scope',
                       war_room_id=war_room_id)

    return {
        'results': results,
        'customers': _customers_summary([cid for cid in target_case_ids if cid in writable]),
    }


def war_room_scope_create_asset(war_room_id, user, asset_payload, case_ids, writable_case_ids,
                                flag_ids=None, flag_reason=None):
    clean = _validate_asset_payload(asset_payload)
    target_case_ids = _validate_id_list(case_ids, 'case_ids', WAR_ROOM_SCOPE_MAX_TARGETS)
    flag_ids = _validate_flag_ids(flag_ids)
    flag_reason = _validate_text(flag_reason, 'flag_reason', _MAX_REASON)
    return _create_object_in_cases(war_room_id, user, 'asset', clean, target_case_ids, writable_case_ids,
                                   flag_ids=flag_ids, flag_reason=flag_reason)


def war_room_scope_create_ioc(war_room_id, user, ioc_payload, case_ids, writable_case_ids):
    clean = _validate_ioc_payload(ioc_payload)
    target_case_ids = _validate_id_list(case_ids, 'case_ids', WAR_ROOM_SCOPE_MAX_TARGETS)
    return _create_object_in_cases(war_room_id, user, 'ioc', clean, target_case_ids, writable_case_ids)


# ---------------------------------------------------------------------------
# Push existing objects to other cases
# ---------------------------------------------------------------------------

def _asset_copy_payload(asset):
    """Fields copied by a push. Never the flags nor custom attributes."""
    clean = {'asset_name': asset.asset_name, 'asset_type_id': asset.asset_type_id}
    for field in ('asset_description', 'asset_ip', 'asset_domain', 'asset_tags',
                  'asset_compromise_status_id', 'analysis_status_id'):
        value = getattr(asset, field, None)
        if value is not None and value != '':
            clean[field] = value
    return clean


def _ioc_copy_payload(ioc):
    clean = {'ioc_value': ioc.ioc_value, 'ioc_type_id': ioc.ioc_type_id}
    for field in ('ioc_tlp_id', 'ioc_description', 'ioc_tags'):
        value = getattr(ioc, field, None)
        if value is not None and value != '':
            clean[field] = value
    return clean


def _push_objects(war_room_id, user, object_type, source_ids, case_ids, readable_case_ids, writable_case_ids):
    id_key = 'asset_id' if object_type == 'asset' else 'ioc_id'
    new_key = f'new_{id_key}'
    existing_key = f'existing_{id_key}'
    source_ids = _validate_id_list(source_ids, f'{id_key}s', WAR_ROOM_SCOPE_MAX_SOURCES)
    target_case_ids = _validate_id_list(case_ids, 'case_ids', WAR_ROOM_SCOPE_MAX_TARGETS)
    readable = set(readable_case_ids)
    writable = set(writable_case_ids)

    if object_type == 'asset':
        sources = {obj.asset_id: obj for obj in war_room_scope_db_assets_by_ids(source_ids)}
    else:
        sources = {obj.ioc_id: obj for obj in war_room_scope_db_iocs_by_ids(source_ids)}

    # Snapshot the copy payloads first: a rollback after a failed create
    # expires the session and would otherwise reload sources lazily.
    payloads = {}
    for source_id in source_ids:
        source = sources.get(source_id)
        if source is None or source.case_id not in readable:
            continue
        payloads[source_id] = _asset_copy_payload(source) if object_type == 'asset' else _ioc_copy_payload(source)

    results = []
    created = 0
    for source_id in source_ids:
        clean = payloads.get(source_id)
        for case_id in target_case_ids:
            row = {id_key: source_id, 'case_id': case_id}
            if clean is None:
                row.update({'status': 'denied', 'message': _SOURCE_NOT_FOUND_MESSAGE})
            elif case_id not in writable:
                row.update({'status': 'denied', 'message': _DENIED_CASE_MESSAGE})
            else:
                if object_type == 'asset':
                    outcome = _create_asset_row(user, case_id, clean)
                else:
                    outcome = _create_ioc_row(case_id, clean)
                row['status'] = outcome['status']
                if outcome['status'] == 'created':
                    created += 1
                    row[new_key] = outcome['object_id']
                elif outcome['status'] == 'exists':
                    row[existing_key] = outcome['object_id']
                if outcome.get('message'):
                    row['message'] = outcome['message']
            results.append(row)

    if created:
        track_activity(f'pushed {created} {object_type}(s) to attached cases from the war room scope',
                       war_room_id=war_room_id)

    return {
        'results': results,
        'customers': _customers_summary([cid for cid in target_case_ids if cid in writable]),
    }


def war_room_scope_push_assets(war_room_id, user, asset_ids, case_ids, readable_case_ids, writable_case_ids):
    return _push_objects(war_room_id, user, 'asset', asset_ids, case_ids, readable_case_ids, writable_case_ids)


def war_room_scope_push_iocs(war_room_id, user, ioc_ids, case_ids, readable_case_ids, writable_case_ids):
    return _push_objects(war_room_id, user, 'ioc', ioc_ids, case_ids, readable_case_ids, writable_case_ids)


# ---------------------------------------------------------------------------
# Bulk flags
# ---------------------------------------------------------------------------

WAR_ROOM_SCOPE_FLAG_ACTIONS = ('set', 'clear')


def war_room_scope_bulk_flag(war_room_id, user_id, asset_ids, flag_id, action, reason, decision_id,
                             readable_case_ids, writable_case_ids):
    """Set (`action='set'`) or remove (`'clear'`) one flag on many assets."""
    asset_ids = _validate_id_list(asset_ids, 'asset_ids', WAR_ROOM_SCOPE_MAX_BULK_FLAG)
    flag_id = _validate_flag_id(flag_id)
    if action not in WAR_ROOM_SCOPE_FLAG_ACTIONS:
        raise BusinessProcessingError(f'action must be one of: {", ".join(WAR_ROOM_SCOPE_FLAG_ACTIONS)}')
    reason = _validate_text(reason, 'reason', _MAX_REASON)
    decision_id = _validate_int_id(decision_id, 'decision_id', allow_none=True)
    if action == 'clear':
        decision_id = None
    if decision_id is not None and not war_room_scope_db_decision_in_room(war_room_id, decision_id):
        raise BusinessProcessingError('Decision not found in this war room')

    readable = set(readable_case_ids)
    writable = set(writable_case_ids)
    assets = {asset.asset_id: asset for asset in war_room_scope_db_assets_by_ids(asset_ids)}
    # Snapshot ownership before any write: a rollback expires the instances.
    owners = {asset_id: asset.case_id for asset_id, asset in assets.items()}

    results = []
    updated = 0
    for asset_id in asset_ids:
        asset = assets.get(asset_id)
        case_id = owners.get(asset_id)
        if asset is None or case_id not in readable:
            results.append({'asset_id': asset_id, 'case_id': None, 'status': 'denied', 'message': 'Asset not found'})
            continue
        if case_id not in writable:
            results.append({'asset_id': asset_id, 'case_id': case_id, 'status': 'denied',
                            'message': _DENIED_CASE_MESSAGE})
            continue
        try:
            if asset_flags_is_unchanged(asset, flag_id, action, reason, decision_id):
                results.append({'asset_id': asset_id, 'case_id': case_id, 'status': 'unchanged'})
                continue
            if action == 'set':
                asset_flags_set_for_asset(asset, flag_id, reason, decision_id, user_id, war_room_id=war_room_id)
            else:
                asset_flags_clear_for_asset(asset, flag_id, reason, user_id, war_room_id=war_room_id)
            updated += 1
            results.append({'asset_id': asset_id, 'case_id': case_id, 'status': 'updated'})
        except BusinessProcessingError as e:
            war_room_scope_db_rollback()
            results.append({'asset_id': asset_id, 'case_id': case_id, 'status': 'error',
                            'message': e.get_message()})
        except Exception:
            _unexpected_error(f'changing the flags of asset {asset_id}')
            results.append({'asset_id': asset_id, 'case_id': case_id, 'status': 'error',
                            'message': 'Unexpected error, the flags were not changed'})

    if updated:
        track_activity(f'changed the flags of {updated} asset(s) from the war room scope', war_room_id=war_room_id)
        call_modules_hook('on_postload_war_room_scope_flag_update', {
            'war_room_id': war_room_id, 'flag_id': flag_id, 'action': action, 'reason': reason,
            'decision_id': decision_id,
            'asset_ids': [row['asset_id'] for row in results if row['status'] == 'updated'],
        })

    return {'results': results}


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------

def _validate_object_type(object_type):
    if object_type not in _STAGED_OBJECT_TYPES:
        raise BusinessProcessingError(f'object_type must be one of {", ".join(_STAGED_OBJECT_TYPES)}')
    return object_type


def _validate_proposed_case_ids(value):
    if value is None:
        return None
    ids = _validate_id_list(value, 'proposed_case_ids', WAR_ROOM_SCOPE_MAX_TARGETS, min_items=0)
    return ids or None


def war_room_scope_serialize_staged(staged, created_by_name=None):
    return {
        'id': staged.id,
        'object_type': staged.object_type,
        'payload': staged.payload,
        'proposed_case_ids': staged.proposed_case_ids,
        'note': staged.note,
        'source_message_id': staged.source_message_id,
        'created_at': staged.created_at.isoformat() if staged.created_at else None,
        'created_by_id': staged.created_by_id,
        'created_by_name': created_by_name if created_by_name is not None else (
            staged.created_by.name if staged.created_by else None),
    }


def war_room_scope_staged_list(war_room_id):
    return [war_room_scope_serialize_staged(staged, created_by_name)
            for staged, created_by_name in war_room_scope_db_staged_list(war_room_id)]


def war_room_scope_staged_get(war_room_id, staged_id):
    staged = war_room_scope_db_staged_get(war_room_id, staged_id)
    if staged is None:
        raise ObjectNotFoundError()
    return staged


def war_room_scope_staged_create(war_room_id, user_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    object_type = _validate_object_type(data.get('object_type'))
    payload = _validate_payload_for_type(object_type, data.get('payload'))
    proposed_case_ids = _validate_proposed_case_ids(data.get('proposed_case_ids'))
    note = _validate_text(data.get('note'), 'note', _MAX_NOTE)
    source_message_id = _validate_int_id(data.get('source_message_id'), 'source_message_id', allow_none=True)
    if source_message_id is not None and not war_room_scope_db_chat_message_in_room(war_room_id, source_message_id):
        raise BusinessProcessingError('Chat message not found in this war room')

    if war_room_scope_db_staged_count(war_room_id) >= WAR_ROOM_SCOPE_MAX_STAGED:
        raise BusinessProcessingError(f'A war room can hold at most {WAR_ROOM_SCOPE_MAX_STAGED} staged objects')

    staged = WarRoomStagedObject(
        war_room_id=war_room_id,
        object_type=object_type,
        payload=payload,
        proposed_case_ids=proposed_case_ids,
        note=note,
        source_message_id=source_message_id,
        created_by_id=user_id,
    )
    staged = war_room_scope_db_staged_add(staged)
    track_activity(f'staged {object_type} "{_staged_label(staged)}"', war_room_id=war_room_id)
    return call_modules_hook('on_postload_war_room_staged_object_create', staged)


def _staged_label(staged):
    payload = staged.payload or {}
    return payload.get('asset_name') if staged.object_type == 'asset' else payload.get('ioc_value')


def war_room_scope_staged_update(war_room_id, staged_id, data):
    if not isinstance(data, dict):
        raise BusinessProcessingError('Invalid request')
    staged = war_room_scope_staged_get(war_room_id, staged_id)
    changes = {}
    if 'payload' in data:
        changes['payload'] = _validate_payload_for_type(staged.object_type, data.get('payload'))
    if 'proposed_case_ids' in data:
        changes['proposed_case_ids'] = _validate_proposed_case_ids(data.get('proposed_case_ids'))
    if 'note' in data:
        changes['note'] = _validate_text(data.get('note'), 'note', _MAX_NOTE)
    for field, value in changes.items():
        setattr(staged, field, value)
    war_room_scope_db_staged_save()
    return call_modules_hook('on_postload_war_room_staged_object_update', staged)


def _staged_hook_ref(war_room_id, staged) -> dict:
    return {'war_room_id': war_room_id, 'staged_id': staged.id, 'object_type': staged.object_type,
            'name': _staged_label(staged)}


def war_room_scope_staged_delete(war_room_id, staged_id):
    staged = war_room_scope_staged_get(war_room_id, staged_id)
    deleted = _staged_hook_ref(war_room_id, staged)
    war_room_scope_db_staged_delete(staged)
    call_modules_hook('on_postload_war_room_staged_object_delete', deleted)


def war_room_scope_staged_push(war_room_id, user, staged_id, case_ids, writable_case_ids):
    """Create the staged object in each target case.

    The staged row is dropped once every target reports created|exists.
    """
    staged = war_room_scope_staged_get(war_room_id, staged_id)
    if case_ids is None:
        case_ids = list(staged.proposed_case_ids or [])
        if not case_ids:
            raise BusinessProcessingError('No target case: provide case_ids')
    target_case_ids = _validate_id_list(case_ids, 'case_ids', WAR_ROOM_SCOPE_MAX_TARGETS)
    # Re-validate: the taxonomy may have changed since the object was staged.
    object_type = staged.object_type
    clean = _validate_payload_for_type(object_type, staged.payload)

    pushed = _staged_hook_ref(war_room_id, staged)
    result = _create_object_in_cases(war_room_id, user, object_type, clean, target_case_ids, writable_case_ids)

    done = all(row['status'] in ('created', 'exists') for row in result['results'])
    if done:
        staged = war_room_scope_staged_get(war_room_id, staged_id)
        war_room_scope_db_staged_delete(staged)
    result['staged_deleted'] = done
    call_modules_hook('on_postload_war_room_staged_object_push',
                      {**pushed, 'results': result['results'], 'staged_deleted': done})
    return result


# ---------------------------------------------------------------------------
# IOC export
# ---------------------------------------------------------------------------

def _csv_safe(value):
    """Neutralise spreadsheet formula injection (OWASP CSV injection)."""
    if value is None:
        return ''
    value = str(value)
    if value.startswith(_CSV_DANGEROUS_PREFIXES):
        return f"'{value}"
    return value


def _tlp_rank(tlp_name):
    if not tlp_name:
        return -1
    return _TLP_RANK.get(tlp_name.strip().lower(), -1)


def _group_iocs_for_export(rows, include_red):
    """Dedup on (type, value); keep the most restrictive TLP of the group."""
    groups = {}
    for row in rows:
        if row.ioc_value is None:
            continue
        key = (row.ioc_type_id, row.ioc_value)
        group = groups.get(key)
        if group is None:
            group = {'value': row.ioc_value, 'type_name': row.ioc_type_name, 'tlp_name': row.tlp_name,
                     'cases': {}}
            groups[key] = group
        elif _tlp_rank(row.tlp_name) > _tlp_rank(group['tlp_name']):
            group['tlp_name'] = row.tlp_name
        group['cases'][row.case_id] = row.case_name

    exported = []
    for group in groups.values():
        if not include_red and _tlp_rank(group['tlp_name']) == _TLP_RANK['red']:
            continue
        exported.append(group)
    exported.sort(key=lambda g: ((g['type_name'] or '').lower(), g['value']))
    return exported


def _export_txt(groups):
    values = []
    seen = set()
    for group in groups:
        value = group['value']
        # One indicator per line: a value spanning lines would corrupt the list.
        if '\n' in value or '\r' in value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return ''.join(f'{value}\n' for value in values)


def _export_csv(groups):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['value', 'type', 'tlp', 'cases'])
    for group in groups:
        cases = '; '.join(f'#{case_id} {name or ""}'.strip() for case_id, name in sorted(group['cases'].items()))
        writer.writerow([_csv_safe(group['value']), _csv_safe(group['type_name']),
                         _csv_safe(group['tlp_name']), _csv_safe(cases)])
    return buffer.getvalue()


def _stix_escape(value):
    return value.replace('\\', '\\\\').replace("'", "\\'")


def _stix_pattern(type_name, value):
    type_name = (type_name or '').strip().lower()
    escaped = _stix_escape(value)
    if type_name in _STIX_HASH_KEYS:
        return f"[file:hashes.{_STIX_HASH_KEYS[type_name]} = '{escaped}']"
    if type_name.startswith('ip-') or type_name == 'ip':
        try:
            address = ipaddress.ip_network(value.strip(), strict=False)
            kind = 'ipv6-addr' if address.version == 6 else 'ipv4-addr'
            return f"[{kind}:value = '{escaped}']"
        except ValueError:
            pass
    if type_name in _STIX_SIMPLE_TYPES:
        return f"[{_STIX_SIMPLE_TYPES[type_name]} = '{escaped}']"
    return f"[x-iris-ioc:value = '{escaped}']"


def _stix_timestamp(now):
    return f'{now.strftime("%Y-%m-%dT%H:%M:%S")}.{now.microsecond // 1000:03d}Z'


def _export_stix(groups, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    timestamp = _stix_timestamp(now)
    objects = []
    for group in groups:
        type_name = group['type_name']
        value = group['value']
        indicator_uuid = uuid.uuid5(_STIX_NAMESPACE, f'{type_name}:{value}')
        indicator = {
            'type': 'indicator',
            'spec_version': '2.1',
            'id': f'indicator--{indicator_uuid}',
            'created': timestamp,
            'modified': timestamp,
            'name': group['value'],
            'description': f'IRIS IOC ({group["type_name"] or "unknown type"})',
            'indicator_types': ['malicious-activity'],
            'pattern': _stix_pattern(group['type_name'], group['value']),
            'pattern_type': 'stix',
            'valid_from': timestamp,
        }
        marking = _STIX_TLP_MARKINGS.get((group['tlp_name'] or '').strip().lower())
        if marking:
            indicator['object_marking_refs'] = [marking]
        objects.append(indicator)
    bundle = {'type': 'bundle', 'id': f'bundle--{uuid.uuid4()}', 'objects': objects}
    return json.dumps(bundle, indent=2)


def war_room_scope_export_iocs(readable_case_ids, export_format, include_red=False):
    """Returns `(content, mimetype, extension)`."""
    if export_format not in WAR_ROOM_SCOPE_EXPORT_FORMATS:
        raise BusinessProcessingError(f'format must be one of {", ".join(WAR_ROOM_SCOPE_EXPORT_FORMATS)}')
    rows = war_room_scope_db_iocs(list(readable_case_ids), limit=_EXPORT_LIMIT)
    groups = _group_iocs_for_export(rows[:_EXPORT_LIMIT], include_red)
    if export_format == 'txt':
        return _export_txt(groups), 'text/plain', 'txt'
    if export_format == 'csv':
        return _export_csv(groups), 'text/csv', 'csv'
    return _export_stix(groups), 'application/json', 'json'
