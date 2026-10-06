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

"""Business layer of the vulnerability catalogue.

Authorization is not checked here. The routes decide who may create,
edit, delete or merge an entry; the finding counts and exposure are
computed over the case ids the caller hands in (`None` meaning
unrestricted) and over the registry scope (a `ManagedAssetViewerScope`,
or `None` when the caller cannot read the registry at all).
"""

import datetime
import json
import re

from app.business.vulnerabilities_cvss import vulnerabilities_cvss_base_score
from app.business.vulnerabilities_cvss import vulnerabilities_cvss_parse
from app.business.vulnerabilities_cvss import vulnerabilities_cvss_severity
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_add
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_aliases
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_case_finding_counts
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_commit
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_count_findings
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_delete
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_exposure_cases
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_exposure_registry
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_find
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_find_many
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_flush
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_get
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_identifier_owner
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_last_private_number
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_merge
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_registry_finding_counts
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_replace_aliases
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_rollback
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_search
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_sortable_fields
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_tlp_exists
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_user_names
from app.iris_engine.module_handler.module_handler import call_modules_hook
from app.iris_engine.utils.tracker import track_activity
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.models.vulnerabilities import VULNERABILITY_CVSS_VERSIONS
from app.models.vulnerabilities import VULNERABILITY_EXPLOIT_MATURITIES
from app.models.vulnerabilities import VULNERABILITY_KINDS
from app.models.vulnerabilities import VULNERABILITY_PATCH_AVAILABILITIES
from app.models.vulnerabilities import VULNERABILITY_PRIVATE_PREFIX
from app.models.vulnerabilities import VULNERABILITY_SEVERITIES
from app.models.vulnerabilities import VULNERABILITY_SOURCES
from app.models.vulnerabilities import Vulnerability

_CVE_RE = re.compile(r'^CVE-\d{4}-\d{4,}$')
_GHSA_RE = re.compile(r'^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$')
_GENERIC_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:\-]{1,63}$')
_CWE_RE = re.compile(r'^CWE-\d{1,6}$')
_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)

_TITLE_MAX_LENGTH = 512
_DESCRIPTION_MAX_LENGTH = 50000
_TAGS_MAX_LENGTH = 1000
_URL_MAX_LENGTH = 2048
_MAX_ALIASES = 50
_MAX_CWES = 50
_MAX_PRODUCTS = 200
_MAX_URLS = 100
_PRODUCT_FIELD_MAX_LENGTH = 256
_ENRICHMENT_MAX_SIZE = 100000
_PER_PAGE_MAX = 200
_PRIVATE_ALLOCATION_ATTEMPTS = 5

_TRUE_VALUES = ('1', 'true', 'yes')
_FALSE_VALUES = ('0', 'false', 'no')


# ---- Parsing helpers (shared with the findings module) ---------------------

def vulnerabilities_normalize_identifier(value, field='identifier') -> str:
    """Canonical form of a vulnerability identifier: `CVE-2024-3400`,
    `GHSA-xxxx-xxxx-xxxx`, otherwise the upper-cased value."""
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    upper = value.upper()
    if upper.startswith('CVE-'):
        if not _CVE_RE.fullmatch(upper):
            raise BusinessProcessingError(f'{field} is not a valid CVE identifier (CVE-YYYY-NNNN)')
        return upper
    if upper.startswith('GHSA-'):
        rest = value[5:].lower()
        if not _GHSA_RE.fullmatch(rest):
            raise BusinessProcessingError(f'{field} is not a valid GitHub advisory identifier (GHSA-xxxx-xxxx-xxxx)')
        return f'GHSA-{rest}'
    if not _GENERIC_RE.fullmatch(value):
        raise BusinessProcessingError(
            f'{field} must be 2 to 64 characters: letters, digits, ".", "_", ":" or "-"')
    return upper


def vulnerabilities_is_private_identifier(identifier) -> bool:
    return identifier.startswith(VULNERABILITY_PRIVATE_PREFIX)


def vulnerabilities_parse_date(value, field):
    """`datetime.date` from an ISO date (or datetime) string; None stays None."""
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be an ISO date (YYYY-MM-DD) or null')
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise BusinessProcessingError(f'{field} must be an ISO date (YYYY-MM-DD) or null')


def vulnerabilities_parse_datetime(value, field):
    """Naive UTC `datetime` from an ISO string; None stays None."""
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be an ISO date-time or null')
    text = value.strip()
    if text.endswith(('Z', 'z')):
        text = f'{text[:-1]}+00:00'
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise BusinessProcessingError(f'{field} must be an ISO date-time or null')
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def vulnerabilities_parse_text(value, field, max_length, required=False):
    if value is None:
        if required:
            raise BusinessProcessingError(f'{field} is required')
        return None
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be a string')
    value = value.strip()
    if required and not value:
        raise BusinessProcessingError(f'{field} is required')
    if len(value) > max_length:
        raise BusinessProcessingError(f'{field} must be at most {max_length} characters')
    return value or None


def vulnerabilities_parse_choice(value, field, choices):
    if value not in choices:
        raise BusinessProcessingError(f'{field} must be one of: {", ".join(choices)}')
    return value


def vulnerabilities_parse_id(value, field):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be an integer or null')
    return value


def vulnerabilities_parse_bool(value, field):
    if not isinstance(value, bool):
        raise BusinessProcessingError(f'{field} must be a boolean')
    return value


def vulnerabilities_parse_query_bool(value, field):
    """Tri-state boolean of a query-string parameter (None when absent).
    A real boolean (MCP tool arguments) is taken as is."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise BusinessProcessingError(f'{field} must be true or false')
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise BusinessProcessingError(f'{field} must be true or false')


def vulnerabilities_parse_query_list(value, field, choices):
    """Comma-separated query-string list (or a list, from MCP tool
    arguments) restricted to `choices`."""
    if value is None or value == '':
        return []
    if isinstance(value, str):
        value = value.split(',')
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BusinessProcessingError(f'{field} must be a comma-separated list')
    items = [item.strip() for item in value if item.strip()]
    for item in items:
        if item not in choices:
            raise BusinessProcessingError(f'{field} values must be among: {", ".join(choices)}')
    return items


def vulnerabilities_isoformat(value):
    return value.isoformat() if value is not None else None


def _parse_float(value, field, lower, upper):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessProcessingError(f'{field} must be a number or null')
    if not lower <= value <= upper:
        raise BusinessProcessingError(f'{field} must be between {lower} and {upper}')
    return float(value)


def _parse_tags(value):
    if value is None:
        return None
    if isinstance(value, list):
        if not all(isinstance(tag, str) for tag in value):
            raise BusinessProcessingError('tags must be a string or a list of strings')
        value = ','.join(tag.strip() for tag in value if tag.strip())
    return vulnerabilities_parse_text(value, 'tags', _TAGS_MAX_LENGTH)


def _parse_cwes(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_CWES:
        raise BusinessProcessingError(f'cwes must be a list of at most {_MAX_CWES} entries')
    result = []
    for item in value:
        if not isinstance(item, str) or not _CWE_RE.fullmatch(item.strip().upper()):
            raise BusinessProcessingError('cwes entries must look like CWE-79')
        cwe = item.strip().upper()
        if cwe not in result:
            result.append(cwe)
    return result or None


def _parse_products(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_PRODUCTS:
        raise BusinessProcessingError(f'affected_products must be a list of at most {_MAX_PRODUCTS} entries')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise BusinessProcessingError('affected_products entries must be objects')
        entry = {}
        for key in ('vendor', 'product', 'versions'):
            text = vulnerabilities_parse_text(item.get(key), f'affected_products.{key}', _PRODUCT_FIELD_MAX_LENGTH)
            if text is not None:
                entry[key] = text
        if not entry.get('product'):
            raise BusinessProcessingError('affected_products entries require a product')
        result.append(entry)
    return result or None


def _parse_urls(value):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_URLS:
        raise BusinessProcessingError(f'reference_urls must be a list of at most {_MAX_URLS} entries')
    result = []
    for item in value:
        if not isinstance(item, str) or len(item.strip()) > _URL_MAX_LENGTH \
                or not _URL_RE.fullmatch(item.strip()):
            raise BusinessProcessingError('reference_urls entries must be http(s) URLs')
        if item.strip() not in result:
            result.append(item.strip())
    return result or None


def _parse_enrichment(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BusinessProcessingError('enrichment must be an object or null')
    if len(json.dumps(value, default=str)) > _ENRICHMENT_MAX_SIZE:
        raise BusinessProcessingError('enrichment is too large')
    return value


def _parse_tlp(value):
    tlp_id = vulnerabilities_parse_id(value, 'tlp_id')
    if tlp_id is not None and not vulnerabilities_db_tlp_exists(tlp_id):
        raise BusinessProcessingError('Unknown tlp_id')
    return tlp_id


def _parse_aliases(value, identifier, vulnerability_id):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > _MAX_ALIASES:
        raise BusinessProcessingError(f'aliases must be a list of at most {_MAX_ALIASES} identifiers')
    result = []
    for item in value:
        alias = vulnerabilities_normalize_identifier(item, 'aliases')
        if vulnerabilities_is_private_identifier(alias):
            raise BusinessProcessingError(f'{VULNERABILITY_PRIVATE_PREFIX}… identifiers cannot be used as aliases')
        if alias == identifier or alias in result:
            continue
        owner = vulnerabilities_db_identifier_owner(alias)
        if owner is not None and owner != vulnerability_id:
            raise BusinessProcessingError(f'{alias} already belongs to another catalogue entry')
        result.append(alias)
    return result


# Simple fields: body key → parser(value)
_FIELD_PARSERS = {
    'title': lambda value: vulnerabilities_parse_text(value, 'title', _TITLE_MAX_LENGTH, required=True),
    'description': lambda value: vulnerabilities_parse_text(value, 'description', _DESCRIPTION_MAX_LENGTH),
    'kind': lambda value: vulnerabilities_parse_choice(value, 'kind', VULNERABILITY_KINDS),
    'epss_score': lambda value: _parse_float(value, 'epss_score', 0, 1),
    'epss_percentile': lambda value: _parse_float(value, 'epss_percentile', 0, 1),
    'epss_date': lambda value: vulnerabilities_parse_date(value, 'epss_date'),
    'kev': lambda value: vulnerabilities_parse_bool(value, 'kev'),
    'kev_date_added': lambda value: vulnerabilities_parse_date(value, 'kev_date_added'),
    'kev_due_date': lambda value: vulnerabilities_parse_date(value, 'kev_due_date'),
    'kev_ransomware': lambda value: vulnerabilities_parse_bool(value, 'kev_ransomware'),
    'exploit_maturity': lambda value: vulnerabilities_parse_choice(
        value, 'exploit_maturity', VULNERABILITY_EXPLOIT_MATURITIES),
    'patch_availability': lambda value: vulnerabilities_parse_choice(
        value, 'patch_availability', VULNERABILITY_PATCH_AVAILABILITIES),
    'cwes': _parse_cwes,
    'affected_products': _parse_products,
    'reference_urls': _parse_urls,
    'published_at': lambda value: vulnerabilities_parse_date(value, 'published_at'),
    'modified_at': lambda value: vulnerabilities_parse_date(value, 'modified_at'),
    'tlp_id': _parse_tlp,
    'tags': _parse_tags,
    'source': lambda value: vulnerabilities_parse_choice(value, 'source', VULNERABILITY_SOURCES),
    'enrichment': _parse_enrichment,
}


def _apply_scoring(vulnerability: Vulnerability, body, creating):
    """CVSS vector / version / score and severity. On update, only what the
    body touches is re-derived."""
    score_touched = creating or 'cvss_vector' in body or 'cvss_score' in body
    if creating or 'cvss_vector' in body:
        vector = body.get('cvss_vector')
        if vector is None or (isinstance(vector, str) and not vector.strip()):
            vulnerability.cvss_vector = None
            version = body.get('cvss_version')
            if version is not None:
                vulnerabilities_parse_choice(version, 'cvss_version', VULNERABILITY_CVSS_VERSIONS)
            vulnerability.cvss_version = version
        else:
            vulnerability.cvss_version, vulnerability.cvss_vector = vulnerabilities_cvss_parse(vector)
            if 'cvss_score' not in body:
                vulnerability.cvss_score = vulnerabilities_cvss_base_score(
                    vulnerability.cvss_version, vulnerability.cvss_vector)
    elif 'cvss_version' in body and vulnerability.cvss_vector is None:
        version = body.get('cvss_version')
        if version is not None:
            vulnerabilities_parse_choice(version, 'cvss_version', VULNERABILITY_CVSS_VERSIONS)
        vulnerability.cvss_version = version

    if 'cvss_score' in body:
        vulnerability.cvss_score = _parse_float(body.get('cvss_score'), 'cvss_score', 0, 10)
        if vulnerability.cvss_score is not None:
            vulnerability.cvss_score = round(vulnerability.cvss_score, 1)

    if body.get('severity') is not None:
        vulnerability.severity = vulnerabilities_parse_choice(body['severity'], 'severity', VULNERABILITY_SEVERITIES)
    elif score_touched or 'severity' in body:
        vulnerability.severity = vulnerabilities_cvss_severity(vulnerability.cvss_score, vulnerability.cvss_version)


def _apply_fields(vulnerability: Vulnerability, body, creating):
    for field, parser in _FIELD_PARSERS.items():
        if field in body:
            setattr(vulnerability, field, parser(body[field]))
        elif creating and field == 'title':
            raise BusinessProcessingError('title is required')
    _apply_scoring(vulnerability, body, creating)


# ---- Serialisation ---------------------------------------------------------

def _counts_dict(case_row, registry_row):
    def value(row, name):
        return getattr(row, name) if row is not None else 0

    return {
        'findings': value(case_row, 'findings') + value(registry_row, 'findings'),
        'open': value(case_row, 'open') + value(registry_row, 'open'),
        'fixed': value(case_row, 'fixed') + value(registry_row, 'fixed'),
        'dismissed': value(case_row, 'dismissed') + value(registry_row, 'dismissed'),
        'exploited': value(case_row, 'exploited') + value(registry_row, 'exploited'),
        'cases': value(case_row, 'cases'),
        'registry_assets': value(registry_row, 'findings'),
    }


def vulnerabilities_serialize(vulnerability: Vulnerability, aliases=None, counts=None, users=None) -> dict:
    users = users or {}
    result = {
        'vulnerability_id': vulnerability.vulnerability_id,
        'vulnerability_uuid': str(vulnerability.vulnerability_uuid) if vulnerability.vulnerability_uuid else None,
        'identifier': vulnerability.identifier,
        'is_private': vulnerability.is_private,
        'kind': vulnerability.kind,
        'title': vulnerability.title,
        'description': vulnerability.description,
        'cvss_version': vulnerability.cvss_version,
        'cvss_vector': vulnerability.cvss_vector,
        'cvss_score': vulnerability.cvss_score,
        'severity': vulnerability.severity,
        'epss_score': vulnerability.epss_score,
        'epss_percentile': vulnerability.epss_percentile,
        'epss_date': vulnerabilities_isoformat(vulnerability.epss_date),
        'kev': vulnerability.kev,
        'kev_date_added': vulnerabilities_isoformat(vulnerability.kev_date_added),
        'kev_due_date': vulnerabilities_isoformat(vulnerability.kev_due_date),
        'kev_ransomware': vulnerability.kev_ransomware,
        'exploit_maturity': vulnerability.exploit_maturity,
        'patch_availability': vulnerability.patch_availability,
        'cwes': vulnerability.cwes or [],
        'affected_products': vulnerability.affected_products or [],
        'reference_urls': vulnerability.reference_urls or [],
        'published_at': vulnerabilities_isoformat(vulnerability.published_at),
        'modified_at': vulnerabilities_isoformat(vulnerability.modified_at),
        'tlp_id': vulnerability.tlp_id,
        'tags': vulnerability.tags,
        'source': vulnerability.source,
        'enrichment': vulnerability.enrichment,
        'aliases': aliases if aliases is not None else [],
        'created_at': vulnerabilities_isoformat(vulnerability.created_at),
        'updated_at': vulnerabilities_isoformat(vulnerability.updated_at),
        'created_by_id': vulnerability.created_by_id,
        'created_by_name': users.get(vulnerability.created_by_id),
        'updated_by_id': vulnerability.updated_by_id,
        'updated_by_name': users.get(vulnerability.updated_by_id),
    }
    if counts is not None:
        result['counts'] = counts
    return result


def vulnerabilities_serialize_short(vulnerability: Vulnerability) -> dict:
    """The catalogue fields a finding embeds."""
    return {
        'vulnerability_id': vulnerability.vulnerability_id,
        'identifier': vulnerability.identifier,
        'is_private': vulnerability.is_private,
        'kind': vulnerability.kind,
        'title': vulnerability.title,
        'cvss_score': vulnerability.cvss_score,
        'cvss_version': vulnerability.cvss_version,
        'severity': vulnerability.severity,
        'epss_score': vulnerability.epss_score,
        'kev': vulnerability.kev,
        'exploit_maturity': vulnerability.exploit_maturity,
        'patch_availability': vulnerability.patch_availability,
    }


# What a private entry keeps outside the instance (AI tools): its handle,
# its scoring and where it is found; never its content.
_PRIVATE_KEPT_FIELDS = frozenset((
    'vulnerability_id', 'vulnerability_uuid', 'identifier', 'is_private', 'kind', 'severity',
    'cvss_score', 'cvss_version', 'tlp_id', 'counts', 'exposure', 'created_at', 'updated_at',
))
VULNERABILITY_PRIVATE_WITHHELD_TITLE = 'Private entry - details withheld'


def _mask_private_entry(entry: dict) -> dict:
    masked = {}
    for key, value in entry.items():
        if key in _PRIVATE_KEPT_FIELDS:
            masked[key] = value
        elif key == 'title':
            masked[key] = VULNERABILITY_PRIVATE_WITHHELD_TITLE
        else:
            masked[key] = [] if isinstance(value, list) else None
    masked['withheld'] = True
    return masked


def vulnerabilities_mask_private(payload):
    """`payload` with every serialised private catalogue entry reduced to
    its identifier and scoring. For results leaving the instance (MCP and
    the in-app LLM): like case export, private entries never do."""
    if isinstance(payload, list):
        return [vulnerabilities_mask_private(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    if payload.get('is_private') is True and 'identifier' in payload:
        return _mask_private_entry(payload)
    return {key: vulnerabilities_mask_private(value) for key, value in payload.items()}


def _serialize_many(rows, case_ids, registry_scope):
    ids = [row.vulnerability_id for row in rows]
    aliases = vulnerabilities_db_aliases(ids)
    case_counts = vulnerabilities_db_case_finding_counts(ids, case_ids)
    registry_counts = vulnerabilities_db_registry_finding_counts(ids, registry_scope) \
        if registry_scope is not None else {}
    users = vulnerabilities_db_user_names(
        [row.created_by_id for row in rows] + [row.updated_by_id for row in rows])
    return [
        vulnerabilities_serialize(
            row, aliases.get(row.vulnerability_id, []),
            _counts_dict(case_counts.get(row.vulnerability_id), registry_counts.get(row.vulnerability_id)),
            users,
        )
        for row in rows
    ]


# ---- Read ------------------------------------------------------------------

def vulnerabilities_get(vulnerability_id) -> Vulnerability:
    vulnerability = vulnerabilities_db_get(vulnerability_id)
    if vulnerability is None:
        raise ObjectNotFoundError()
    return vulnerability


def vulnerabilities_get_public(vulnerability: Vulnerability, case_ids, registry_scope) -> dict:
    return _serialize_many([vulnerability], case_ids, registry_scope)[0]


def vulnerabilities_lookup(identifier, case_ids, registry_scope, hide_private_content=False):
    """The entry an identifier (or alias) designates, or None. With
    `hide_private_content`, a private entry only answers to its own
    identifier: its aliases are content too."""
    normalized = vulnerabilities_normalize_identifier(identifier)
    vulnerability = vulnerabilities_db_find(normalized)
    if vulnerability is None:
        return None
    if hide_private_content and vulnerability.is_private and vulnerability.identifier != normalized:
        return None
    return vulnerabilities_get_public(vulnerability, case_ids, registry_scope)


def vulnerabilities_search(args, pagination, case_ids, registry_scope, hide_private_content=False) -> dict:
    """One page of the catalogue. `args` is the query-string mapping. With
    `hide_private_content` (results leaving the instance), private entries
    are neither matched nor ordered on their content."""
    filters = {
        'search': vulnerabilities_parse_text(args.get('search'), 'search', 256),
        'severities': vulnerabilities_parse_query_list(args.get('severity'), 'severity', VULNERABILITY_SEVERITIES),
        'kinds': vulnerabilities_parse_query_list(args.get('kind'), 'kind', VULNERABILITY_KINDS),
        'kev': vulnerabilities_parse_query_bool(args.get('kev'), 'kev'),
        'is_private': vulnerabilities_parse_query_bool(args.get('private'), 'private'),
        'affected': vulnerabilities_parse_query_bool(args.get('affected'), 'affected'),
        'private_content_hidden': hide_private_content,
    }
    page = pagination.get_page() or 1
    per_page = pagination.get_per_page() or 25
    if page < 1 or per_page < 1:
        raise BusinessProcessingError('page and per_page must be positive')
    per_page = min(per_page, _PER_PAGE_MAX)
    order_by = pagination.get_order_by() or 'updated_at'
    if order_by not in vulnerabilities_db_sortable_fields():
        raise BusinessProcessingError(
            f'order_by must be one of: {", ".join(vulnerabilities_db_sortable_fields())}')
    if hide_private_content and order_by == 'title':
        raise BusinessProcessingError('order_by title is not available here')
    direction = (pagination.get_direction() or 'desc').lower()
    if direction not in ('asc', 'desc'):
        raise BusinessProcessingError('sort_dir must be asc or desc')

    rows, total = vulnerabilities_db_search(filters, page, per_page, order_by, direction, case_ids)
    last_page = max(1, (total + per_page - 1) // per_page)
    return {
        'total': total,
        'data': _serialize_many(rows, case_ids, registry_scope),
        'last_page': last_page,
        'current_page': page,
        'next_page': page + 1 if page < last_page else None,
    }


def vulnerabilities_exposure(vulnerability: Vulnerability, case_ids, registry_scope) -> dict:
    """Where an entry is found: per case (over `case_ids`) and on the
    registry (`registry_scope`, None skipping it)."""
    cases = [{
        'case_id': row.case_id,
        'case_name': row.case_name,
        'customer_id': row.customer_id,
        'customer_name': row.customer_name,
        'case_closed': row.close_date is not None,
        'findings': row.findings,
        'open': row.open,
        'fixed': row.fixed,
        'dismissed': row.dismissed,
        'exploited': row.exploited,
    } for row in vulnerabilities_db_exposure_cases(vulnerability.vulnerability_id, case_ids)]

    registry = []
    if registry_scope is not None:
        registry = [{
            'finding_id': finding.finding_id,
            'managed_asset_id': finding.managed_asset_id,
            'asset_name': asset_name,
            'customer_id': client_id,
            'customer_name': customer_name,
            'criticality': criticality,
            'remediation_status': finding.remediation_status,
            'exploitation_status': finding.exploitation_status,
            'due_date': vulnerabilities_isoformat(finding.due_date),
        } for finding, asset_name, client_id, customer_name, criticality
            in vulnerabilities_db_exposure_registry(vulnerability.vulnerability_id, registry_scope)]

    return {
        'vulnerability_id': vulnerability.vulnerability_id,
        'identifier': vulnerability.identifier,
        'cases': cases,
        'registry': registry,
        'totals': {
            'cases': len(cases),
            'case_findings': sum(case['findings'] for case in cases),
            'open': sum(case['open'] for case in cases)
            + sum(1 for entry in registry if entry['remediation_status'] in ('under-analysis', 'affected',
                                                                           'mitigated')),
            'exploited': sum(case['exploited'] for case in cases)
            + sum(1 for entry in registry if entry['exploitation_status'] == 'exploited'),
            'registry_assets': len(registry),
        },
    }


# ---- Write -----------------------------------------------------------------

def _allocate_private_identifier():
    year = datetime.datetime.utcnow().year
    return f'{VULNERABILITY_PRIVATE_PREFIX}{year}-{vulnerabilities_db_last_private_number(year) + 1:04d}'


def _default_kind(identifier, is_private):
    if is_private:
        return 'other'
    return 'cve' if identifier.startswith('CVE-') else 'advisory'


def _create_row(body, user_id, identifier, is_private, aliases):
    vulnerability = Vulnerability(identifier=identifier, is_private=is_private,
                                  created_by_id=user_id, updated_by_id=user_id)
    _apply_fields(vulnerability, body, creating=True)
    if 'kind' not in body:
        vulnerability.kind = _default_kind(identifier, is_private)
    vulnerabilities_db_add(vulnerability)
    if not vulnerabilities_db_flush():
        return None
    vulnerabilities_db_replace_aliases(vulnerability.vulnerability_id, aliases)
    if not vulnerabilities_db_commit():
        return None
    return vulnerability


def vulnerabilities_create(body, user_id) -> Vulnerability:
    """Create a catalogue entry. A private entry gets the next free
    `IRIS-VULN-<year>-NNNN` identifier; a public one must name itself."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    is_private = vulnerabilities_parse_bool(body.get('is_private', False), 'is_private')

    if is_private:
        if body.get('identifier'):
            raise BusinessProcessingError('Private entries get an allocated identifier: do not supply one')
        identifier = None
    else:
        if not body.get('identifier'):
            raise BusinessProcessingError('identifier is required for a public entry')
        identifier = vulnerabilities_normalize_identifier(body['identifier'])
        if vulnerabilities_is_private_identifier(identifier):
            raise BusinessProcessingError(f'The {VULNERABILITY_PRIVATE_PREFIX} prefix is reserved to private entries')
        if vulnerabilities_db_identifier_owner(identifier) is not None:
            raise BusinessProcessingError(f'{identifier} is already in the catalogue')

    # Validate everything once before allocating anything.
    _apply_fields(Vulnerability(), body, creating=True)
    aliases = _parse_aliases(body.get('aliases'), identifier, None)

    attempts = _PRIVATE_ALLOCATION_ATTEMPTS if is_private else 1
    vulnerability = None
    for _ in range(attempts):
        current = identifier if not is_private else _allocate_private_identifier()
        vulnerability = _create_row(body, user_id, current, is_private, aliases)
        if vulnerability is not None:
            break
    if vulnerability is None:
        vulnerabilities_db_rollback()
        raise BusinessProcessingError('Unable to create the vulnerability (identifier or alias already in use)')

    track_activity(f'vulnerability {vulnerability.identifier} created', ctx_less=True)
    return call_modules_hook('on_postload_vulnerability_create', vulnerability)


def vulnerabilities_get_or_create(identifier, title, user_id) -> Vulnerability:
    """Entry of a public identifier (or alias), created on the fly with a
    minimal record when unknown (quick add from a finding)."""
    normalized = vulnerabilities_normalize_identifier(identifier)
    if vulnerabilities_is_private_identifier(normalized):
        existing = vulnerabilities_db_find(normalized)
        if existing is None:
            raise BusinessProcessingError(f'Unknown private vulnerability {normalized}')
        return existing
    existing = vulnerabilities_db_find(normalized)
    if existing is not None:
        return existing
    title = vulnerabilities_parse_text(title, 'title', _TITLE_MAX_LENGTH) or normalized
    vulnerability = Vulnerability(identifier=normalized, is_private=False, title=title,
                                  kind=_default_kind(normalized, False), severity='unknown',
                                  created_by_id=user_id, updated_by_id=user_id)
    vulnerabilities_db_add(vulnerability)
    if not vulnerabilities_db_commit():
        # Created concurrently: use the winner.
        existing = vulnerabilities_db_find(normalized)
        if existing is None:
            raise BusinessProcessingError(f'Unable to create {normalized}')
        return existing
    track_activity(f'vulnerability {normalized} created', ctx_less=True)
    return call_modules_hook('on_postload_vulnerability_create', vulnerability)


def vulnerabilities_update(vulnerability: Vulnerability, body, user_id) -> Vulnerability:
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    try:
        _update(vulnerability, body, user_id)
    except BusinessProcessingError:
        # Leave nothing half-applied in the session.
        vulnerabilities_db_rollback()
        raise
    track_activity(f'vulnerability {vulnerability.identifier} updated', ctx_less=True)
    return call_modules_hook('on_postload_vulnerability_update', vulnerability)


def _update(vulnerability: Vulnerability, body, user_id):
    if 'is_private' in body and body['is_private'] != vulnerability.is_private:
        raise BusinessProcessingError('is_private cannot be changed')
    if 'identifier' in body and body['identifier'] is not None:
        identifier = vulnerabilities_normalize_identifier(body['identifier'])
        if identifier != vulnerability.identifier:
            if vulnerability.is_private:
                raise BusinessProcessingError('The identifier of a private entry cannot be changed')
            if vulnerabilities_is_private_identifier(identifier):
                raise BusinessProcessingError(
                    f'The {VULNERABILITY_PRIVATE_PREFIX} prefix is reserved to private entries')
            owner = vulnerabilities_db_identifier_owner(identifier)
            if owner is not None and owner != vulnerability.vulnerability_id:
                raise BusinessProcessingError(f'{identifier} already belongs to another catalogue entry')
            vulnerability.identifier = identifier

    aliases = None
    if 'aliases' in body:
        aliases = _parse_aliases(body['aliases'], vulnerability.identifier, vulnerability.vulnerability_id)

    _apply_fields(vulnerability, body, creating=False)
    vulnerability.updated_by_id = user_id
    vulnerability.updated_at = datetime.datetime.utcnow()
    if aliases is not None:
        # An identifier renamed into one of its own aliases drops that alias.
        vulnerabilities_db_replace_aliases(vulnerability.vulnerability_id,
                                           [alias for alias in aliases if alias != vulnerability.identifier])
    if not vulnerabilities_db_commit():
        raise BusinessProcessingError('Unable to update the vulnerability (identifier or alias already in use)')


# ---- Case transfer ---------------------------------------------------------

# What a public catalogue entry carries into another instance when a case
# referencing it is imported there (mirrors the `vulnerability` lookup of
# `transfer_spec`). Private entries never leave the instance.
_TRANSFER_FIELDS = ('title', 'description', 'kind', 'cvss_vector', 'cvss_version', 'cvss_score', 'severity',
                    'cwes', 'affected_products', 'reference_urls', 'published_at', 'modified_at', 'kev',
                    'exploit_maturity', 'patch_availability')


def _transfer_identifier(name):
    """Normalised public identifier of a bundle entry, None if unusable."""
    try:
        identifier = vulnerabilities_normalize_identifier(name)
    except BusinessProcessingError:
        return None
    return None if vulnerabilities_is_private_identifier(identifier) else identifier


def vulnerabilities_transfer_match(names) -> dict:
    """{bundle name: target entry id} for the names this catalogue already
    knows, by identifier or alias. Private identifiers never match: they
    are local to each instance."""
    identifiers = {name: _transfer_identifier(name) for name in names if isinstance(name, str)}
    found = vulnerabilities_db_find_many({value for value in identifiers.values() if value is not None})
    return {name: found[identifier].vulnerability_id
            for name, identifier in identifiers.items()
            if identifier is not None and identifier in found and not found[identifier].is_private}


def vulnerabilities_transfer_create(entry) -> int:
    """Create (flush, no commit) the public entry a bundle names, with its
    fields validated like a manual creation. Returns its id."""
    identifier = _transfer_identifier(entry.get('name'))
    if identifier is None:
        raise BusinessProcessingError(f'Invalid vulnerability identifier in the bundle: {entry.get("name")!r}')
    body = {field: entry[field] for field in _TRANSFER_FIELDS if field in entry}
    if not body.get('title'):
        body['title'] = identifier
    vulnerability = Vulnerability(identifier=identifier, is_private=False, source='import')
    _apply_fields(vulnerability, body, creating=True)
    if 'kind' not in body:
        vulnerability.kind = _default_kind(identifier, False)
    vulnerabilities_db_add(vulnerability)
    if not vulnerabilities_db_flush():
        raise BusinessProcessingError(f'Unable to create {identifier}')
    return vulnerability.vulnerability_id


def vulnerabilities_delete(vulnerability: Vulnerability) -> None:
    count = vulnerabilities_db_count_findings(vulnerability.vulnerability_id)
    if count:
        raise BusinessProcessingError(
            f'{vulnerability.identifier} is used by {count} finding(s): merge it into another entry instead',
            data={'findings': count})
    deleted = {'vulnerability_id': vulnerability.vulnerability_id, 'identifier': vulnerability.identifier,
               'is_private': vulnerability.is_private}
    if not vulnerabilities_db_delete(vulnerability):
        raise BusinessProcessingError('Unable to delete the vulnerability')
    track_activity(f'vulnerability {deleted["identifier"]} deleted', ctx_less=True)
    call_modules_hook('on_postload_vulnerability_delete', deleted)


def vulnerabilities_merge(source: Vulnerability, body, user_id):
    """Fold `source` into the entry `body.target_id`: findings move over,
    the source identifier and aliases become aliases of the target, and
    the source is deleted. Returns `(target, summary)`."""
    if not isinstance(body, dict):
        raise BusinessProcessingError('Invalid body')
    target_id = vulnerabilities_parse_id(body.get('target_id'), 'target_id')
    if target_id is None:
        raise BusinessProcessingError('target_id is required')
    if target_id == source.vulnerability_id:
        raise BusinessProcessingError('An entry cannot be merged into itself')
    target = vulnerabilities_db_get(target_id)
    if target is None:
        raise BusinessProcessingError('Unknown target vulnerability')

    source_identifier = source.identifier
    source_id = source.vulnerability_id
    existing = vulnerabilities_db_aliases([source_id, target_id])
    merged_aliases = list(existing[target_id])
    for alias in [source_identifier] + existing[source_id]:
        if alias != target.identifier and alias not in merged_aliases:
            merged_aliases.append(alias)

    moved, dropped = vulnerabilities_db_merge(source_id, target_id)
    vulnerabilities_db_replace_aliases(target_id, merged_aliases)
    target.updated_by_id = user_id
    target.updated_at = datetime.datetime.utcnow()
    if not vulnerabilities_db_commit():
        raise BusinessProcessingError('Unable to merge the vulnerabilities')

    track_activity(f'vulnerability {source_identifier} merged into {target.identifier} '
                   f'({moved} finding(s) moved, {dropped} duplicate(s) dropped)', ctx_less=True)
    summary = {'moved': moved, 'dropped': dropped, 'source_identifier': source_identifier}
    call_modules_hook('on_postload_vulnerability_merge', {'vulnerability': target, 'source_id': source_id, **summary})
    return target, summary
