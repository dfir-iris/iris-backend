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

"""Synchronisation of public CVE catalogue entries with cve.org.

`vulnerabilities_cve_parse` turns a CVE JSON 5 record into catalogue
fields: the CNA container first, the ADP containers (CISA-ADP chiefly)
filling what the CNA left out, plus the CISA KEV listing and SSVC
exploitation status CISA publishes there.

A sync never clobbers an analyst's edit. The values of the previous sync
are kept in `enrichment.cve_org.values`; a field is overwritten only when
it is still empty, or still equal to what the last sync wrote. Anything
else is reported as kept, unless the sync is forced. Exploit maturity
and the KEV flag only ever move up: the record says nothing about a
proof of concept or exploitation the analyst saw themselves.

Private (`IRIS-VULN-…`) entries and non-CVE identifiers are never sent
anywhere.
"""

import datetime
import re

from app import app
from app.business.server_settings import get_srv_settings
from app.business.vulnerabilities import vulnerabilities_isoformat
from app.business.vulnerabilities import vulnerabilities_normalize_identifier
from app.business.vulnerabilities import vulnerabilities_update
from app.business.vulnerabilities_cvss import vulnerabilities_cvss_parse
from app.datamgmt.vulnerabilities.vulnerabilities_db import vulnerabilities_db_find
from app.iris_engine.vulnerabilities.cve_org import CveOrgError
from app.iris_engine.vulnerabilities.cve_org import cve_org_fetch
from app.logger import logger
from app.models.errors import BusinessProcessingError
from app.models.vulnerabilities import VULNERABILITY_EXPLOIT_MATURITIES
from app.models.vulnerabilities import VULNERABILITY_SEVERITIES
from app.models.vulnerabilities import Vulnerability

_CVE_RE = re.compile(r'^CVE-\d{4}-\d{4,}$')
_CWE_RE = re.compile(r'^CWE-\d{1,6}$')
_URL_RE = re.compile(r'^https?://\S+$', re.IGNORECASE)

_TITLE_MAX_LENGTH = 512
_DESCRIPTION_MAX_LENGTH = 50000
_PRODUCT_FIELD_MAX_LENGTH = 256
_URL_MAX_LENGTH = 2048
_MAX_CWES = 50
_MAX_PRODUCTS = 200
_MAX_URLS = 100
_MAX_VERSION_RANGES = 12

# Best CVSS version first.
_CVSS_METRIC_KEYS = (('cvssV4_0', '4.0'), ('cvssV3_1', '3.1'), ('cvssV3_0', '3.0'), ('cvssV2_0', '2.0'))
_SSVC_EXPLOITATION = {'active': 'in-the-wild', 'poc': 'poc', 'none': 'none'}
_BLANK_NAMES = ('', 'n/a', 'unknown', '*', '-')

# Synchronised fields; the CVSS ones move together.
_SCORING_FIELDS = ('cvss_vector', 'cvss_version', 'cvss_score', 'severity')
_PLAIN_FIELDS = ('title', 'description', 'cwes', 'affected_products', 'reference_urls',
                 'published_at', 'modified_at', 'kev_date_added')

# Quick add from a finding must not hang on a slow API.
_QUICK_ADD_TIMEOUT_RATIO = 0.5


def vulnerabilities_cve_sync_enabled() -> bool:
    return bool(app.config.get('CVE_SYNC_ENABLED', True))


# ---- Record parsing --------------------------------------------------------

def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _truncate(value, limit):
    if value is None or len(value) <= limit:
        return value
    return f'{value[:limit - 1].rstrip()}…'


def _english(entries):
    """`value` of the English entry of a `descriptions`-like list (else the first)."""
    if not isinstance(entries, list):
        return None
    fallback = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = _text(entry.get('value'))
        if value is None:
            continue
        lang = str(entry.get('lang') or '').lower()
        if lang == 'en' or lang.startswith('en-') or lang.startswith('en_'):
            return value
        fallback = fallback or value
    return fallback


def _first_sentence(text):
    first_line = text.strip().split('\n', 1)[0].strip()
    match = re.match(r'^(.+?[.!?])(\s|$)', first_line)
    return match.group(1) if match else first_line


def _containers(record):
    containers = record.get('containers') if isinstance(record.get('containers'), dict) else {}
    cna = containers.get('cna') if isinstance(containers.get('cna'), dict) else {}
    adps = [adp for adp in (containers.get('adp') or []) if isinstance(adp, dict)]
    return cna, adps


def _scoring_from(metrics):
    """Best parseable CVSS of a container's metrics, or None."""
    if not isinstance(metrics, list):
        return None
    for key, version in _CVSS_METRIC_KEYS:
        for metric in metrics:
            data = metric.get(key) if isinstance(metric, dict) else None
            if not isinstance(data, dict):
                continue
            score = data.get('baseScore')
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
                score = None
            vector = _text(data.get('vectorString'))
            parsed_version = version
            if vector is not None:
                try:
                    parsed_version, vector = vulnerabilities_cvss_parse(vector)
                except BusinessProcessingError:
                    vector = None
            if vector is None and score is None:
                continue
            severity = str(data.get('baseSeverity') or '').lower()
            return {
                'cvss_vector': vector,
                'cvss_version': parsed_version,
                'cvss_score': round(float(score), 1) if score is not None else None,
                'severity': severity if severity in VULNERABILITY_SEVERITIES else None,
            }
    return None


def _cwes_from(container):
    result = []
    for problem in container.get('problemTypes') or []:
        if not isinstance(problem, dict):
            continue
        for description in problem.get('descriptions') or []:
            if not isinstance(description, dict):
                continue
            cwe = str(description.get('cweId') or '').strip().upper()
            if _CWE_RE.fullmatch(cwe) and cwe not in result:
                result.append(cwe)
    return result


def _version_range(entry):
    version = _text(entry.get('version'))
    if version and version.lower() in _BLANK_NAMES:
        version = None
    less_than = _text(entry.get('lessThan'))
    less_equal = _text(entry.get('lessThanOrEqual'))
    upper = None
    bound = None
    # `*` (or a wildcard like `log4j-core*`) means "no upper bound".
    if less_than and '*' not in less_than:
        upper, bound = f'< {less_than}', less_than
    elif less_equal and '*' not in less_equal:
        upper, bound = f'<= {less_equal}', less_equal
    if version in (None, '0', bound) and upper:
        return upper
    if version and upper:
        return f'{version} to {upper}'
    if version and (less_than or less_equal):
        return f'>= {version}'
    return version


def _products_from(container):
    products = []
    for affected in container.get('affected') or []:
        if not isinstance(affected, dict):
            continue
        product = _text(affected.get('product')) or _text(affected.get('packageName'))
        if product is None or product.lower() in _BLANK_NAMES:
            continue
        vendor = _text(affected.get('vendor'))
        if vendor is not None and vendor.lower() in _BLANK_NAMES:
            vendor = None
        ranges = []
        for entry in affected.get('versions') or []:
            if not isinstance(entry, dict) or entry.get('status') != 'affected':
                continue
            described = _version_range(entry)
            if described and described not in ranges:
                ranges.append(described)
        if not ranges and affected.get('defaultStatus') != 'affected':
            # Every listed version is unaffected (or nothing is said): not an affected product.
            continue
        if len(ranges) > _MAX_VERSION_RANGES:
            ranges = [*ranges[:_MAX_VERSION_RANGES], f'+{len(ranges) - _MAX_VERSION_RANGES} more']
        entry = {'product': _truncate(product, _PRODUCT_FIELD_MAX_LENGTH)}
        if vendor:
            entry['vendor'] = _truncate(vendor, _PRODUCT_FIELD_MAX_LENGTH)
        if ranges:
            entry['versions'] = _truncate(', '.join(ranges), _PRODUCT_FIELD_MAX_LENGTH)
        if entry not in products:
            products.append(entry)
        if len(products) >= _MAX_PRODUCTS:
            break
    return products


def _urls_from(containers):
    urls = []
    for container in containers:
        for reference in container.get('references') or []:
            url = _text(reference.get('url')) if isinstance(reference, dict) else None
            if url is None or len(url) > _URL_MAX_LENGTH or not _URL_RE.fullmatch(url) or url in urls:
                continue
            urls.append(url)
            if len(urls) >= _MAX_URLS:
                return urls
    return urls


def _cisa_signals(adps):
    """`(kev_date_added or None, kev_listed, ssvc exploitation or None)` of the ADP containers."""
    kev_listed = False
    kev_date = None
    exploitation = None
    for adp in adps:
        for metric in adp.get('metrics') or []:
            other = metric.get('other') if isinstance(metric, dict) else None
            if not isinstance(other, dict) or not isinstance(other.get('content'), dict):
                continue
            content = other['content']
            if other.get('type') == 'kev':
                kev_listed = True
                kev_date = kev_date or _date(content.get('dateAdded'))
            elif other.get('type') == 'ssvc':
                for option in content.get('options') or []:
                    if isinstance(option, dict) and isinstance(option.get('Exploitation'), str):
                        exploitation = exploitation or option['Exploitation'].strip().lower()
    return kev_date, kev_listed, exploitation


def _date(value):
    """ISO date (`YYYY-MM-DD`) of a record timestamp, or None."""
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def vulnerabilities_cve_parse(record) -> dict:
    """`{'meta': {...}, 'fields': {...}}` of a CVE JSON 5 record. `fields`
    is a catalogue body (dates as ISO strings); `meta` carries the record
    state, assigner and timestamps. A rejected record raises."""
    if not isinstance(record, dict) or not isinstance(record.get('cveMetadata'), dict):
        raise BusinessProcessingError('Not a CVE record')
    metadata = record['cveMetadata']
    cve_id = str(metadata.get('cveId') or '').upper()
    state = str(metadata.get('state') or '').upper()
    cna, adps = _containers(record)
    if state == 'REJECTED':
        reason = _english(cna.get('rejectedReasons'))
        raise BusinessProcessingError(f'{cve_id} has been rejected by the CVE Program'
                                      + (f': {reason}' if reason else ''))

    description = _english(cna.get('descriptions'))
    title = _text(cna.get('title')) or (_first_sentence(description) if description else None) or cve_id

    scoring = _scoring_from(cna.get('metrics'))
    for adp in adps:
        scoring = scoring or _scoring_from(adp.get('metrics'))
    scoring = scoring or {'cvss_vector': None, 'cvss_version': None, 'cvss_score': None, 'severity': None}

    cwes = _cwes_from(cna)
    if not cwes:
        for adp in adps:
            cwes.extend(cwe for cwe in _cwes_from(adp) if cwe not in cwes)
    products = _products_from(cna)
    if not products:
        for adp in adps:
            products = products or _products_from(adp)

    kev_date, kev_listed, exploitation = _cisa_signals(adps)
    exploit_maturity = 'in-the-wild' if kev_listed else _SSVC_EXPLOITATION.get(exploitation, 'unknown')

    fields = {
        'title': _truncate(title, _TITLE_MAX_LENGTH),
        'description': _truncate(description, _DESCRIPTION_MAX_LENGTH),
        **scoring,
        'cwes': cwes[:_MAX_CWES],
        'affected_products': products,
        'reference_urls': _urls_from([cna, *adps]),
        'published_at': _date(metadata.get('datePublished')) or _date(cna.get('datePublic')),
        'modified_at': _date(metadata.get('dateUpdated')),
        'kev': kev_listed,
        'kev_date_added': kev_date,
        'exploit_maturity': exploit_maturity,
    }
    meta = {
        'cve_id': cve_id,
        'state': state or None,
        'assigner': _text(metadata.get('assignerShortName')),
        'date_published': _text(metadata.get('datePublished')),
        'date_updated': _text(metadata.get('dateUpdated')),
    }
    return {'meta': meta, 'fields': fields}


# ---- Fetching --------------------------------------------------------------

def _proxies():
    settings = get_srv_settings()
    proxies = {}
    if settings is not None:
        if getattr(settings, 'http_proxy', None):
            proxies['http'] = settings.http_proxy
        if getattr(settings, 'https_proxy', None):
            proxies['https'] = settings.https_proxy
    return proxies or None


def _cve_identifier(identifier):
    normalized = vulnerabilities_normalize_identifier(identifier)
    if not _CVE_RE.fullmatch(normalized):
        raise BusinessProcessingError('Only CVE identifiers (CVE-YYYY-NNNN) can be synchronised from cve.org')
    return normalized


def _fetch(cve_id, timeout=None):
    if not vulnerabilities_cve_sync_enabled():
        raise BusinessProcessingError('CVE synchronisation is disabled on this instance (IRIS_CVE_SYNC_ENABLED)')
    if timeout is None:
        timeout = float(app.config.get('CVE_API_TIMEOUT_SECONDS', 10))
    try:
        record = cve_org_fetch(app.config.get('CVE_API_URL') or 'https://cveawg.mitre.org/api/cve/',
                               cve_id, timeout=timeout, proxies=_proxies())
    except CveOrgError as e:
        raise BusinessProcessingError(e.message, data={'not_found': e.not_found})
    return vulnerabilities_cve_parse(record)


def vulnerabilities_cve_lookup(identifier) -> dict:
    """Catalogue fields of a CVE as cve.org publishes it, nothing saved.
    `existing_id` names the catalogue entry already holding it, if any;
    `enrichment` is what a create should carry so that the next sync
    knows which values came from cve.org."""
    cve_id = _cve_identifier(identifier)
    parsed = _fetch(cve_id)
    existing = vulnerabilities_db_find(cve_id)
    return {
        'identifier': cve_id,
        **parsed['meta'],
        'fields': {'kind': 'cve', **parsed['fields']},
        'existing_id': existing.vulnerability_id if existing is not None else None,
        'enrichment': {'cve_org': _sync_marker(parsed)},
    }


# ---- Merging into an entry -------------------------------------------------

def _sync_marker(parsed):
    return {
        'synced_at': f'{datetime.datetime.utcnow().replace(microsecond=0).isoformat()}Z',
        'state': parsed['meta']['state'],
        'assigner': parsed['meta']['assigner'],
        'date_updated': parsed['meta']['date_updated'],
        'values': parsed['fields'],
    }


def _current(vulnerability: Vulnerability, field):
    value = getattr(vulnerability, field)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return vulnerabilities_isoformat(value)[:10]
    if field == 'cvss_score' and value is not None:
        return round(float(value), 1)
    return value


def _empty_to_none(value):
    return None if value in ('', []) else value


def _is_blank(vulnerability: Vulnerability, field, value):
    if value in (None, '', []):
        return True
    if field == 'title':
        return value == vulnerability.identifier
    return False


def _scoring_blank(current):
    return current['cvss_vector'] is None and current['cvss_score'] is None


def _scoring_key(values):
    return tuple(values.get(field) for field in ('cvss_vector', 'cvss_version', 'cvss_score'))


def _maturity_rank(value):
    return VULNERABILITY_EXPLOIT_MATURITIES.index(value) if value in VULNERABILITY_EXPLOIT_MATURITIES else 0


def _plan(vulnerability: Vulnerability, fields, previous, force):
    """`(body, updated, kept)`: what to write and the field names moved / held."""
    body = {}
    updated = []
    kept = []

    for field in _PLAIN_FIELDS:
        new = _empty_to_none(fields.get(field))
        current = _empty_to_none(_current(vulnerability, field))
        if current == new or (new is None and field == 'title'):
            continue
        overwrite = force or _is_blank(vulnerability, field, current) \
            or (previous is not None and _empty_to_none(previous.get(field)) == current)
        if field == 'kev_date_added' and not fields.get('kev') and not force:
            overwrite = False
        if overwrite:
            body[field] = new
            updated.append(field)
        else:
            kept.append(field)

    current = {field: _current(vulnerability, field) for field in _SCORING_FIELDS}
    new = {field: fields.get(field) for field in _SCORING_FIELDS}
    if new['cvss_vector'] or new['cvss_score'] is not None:
        # A record without severity lets the server derive it from the score.
        severity_matches = new['severity'] is None or current['severity'] == new['severity']
        if _scoring_key(current) != _scoring_key(new) or not severity_matches:
            untouched = previous is not None and _scoring_key(previous) == _scoring_key(current) \
                and previous.get('severity') in (None, current['severity'])
            if force or _scoring_blank(current) or untouched:
                body['cvss_vector'] = new['cvss_vector']
                if new['cvss_vector'] is None:
                    body['cvss_version'] = new['cvss_version']
                body['cvss_score'] = new['cvss_score']
                body['severity'] = new['severity']
                updated.append('cvss')
            else:
                kept.append('cvss')

    if fields.get('kev') and not vulnerability.kev:
        body['kev'] = True
        updated.append('kev')
    if _maturity_rank(fields.get('exploit_maturity')) > _maturity_rank(vulnerability.exploit_maturity):
        body['exploit_maturity'] = fields['exploit_maturity']
        updated.append('exploit_maturity')
    if vulnerability.kind in ('other', 'advisory') and _is_blank(vulnerability, 'title', vulnerability.title):
        body['kind'] = 'cve'
    return body, updated, kept


def vulnerabilities_cve_sync(vulnerability: Vulnerability, user_id, force=False, timeout=None) -> dict:
    """Refresh a public CVE entry from cve.org. Returns the sync report:
    `updated_fields`, `kept_fields` (edited locally, left alone) and the
    record metadata."""
    if vulnerability.is_private:
        raise BusinessProcessingError('Private entries never leave this instance and cannot be synchronised')
    cve_id = _cve_identifier(vulnerability.identifier)
    parsed = _fetch(cve_id, timeout)
    if parsed['meta']['cve_id'] and parsed['meta']['cve_id'] != cve_id:
        raise BusinessProcessingError(f'cve.org answered {parsed["meta"]["cve_id"]} for {cve_id}')

    enrichment = dict(vulnerability.enrichment) if isinstance(vulnerability.enrichment, dict) else {}
    previous = enrichment.get('cve_org', {}).get('values') if isinstance(enrichment.get('cve_org'), dict) else None
    body, updated, kept = _plan(vulnerability, parsed['fields'], previous, force)
    enrichment['cve_org'] = _sync_marker(parsed)
    body['enrichment'] = enrichment
    if updated and vulnerability.source == 'manual':
        body['source'] = 'import'
    vulnerabilities_update(vulnerability, body, user_id)
    return {
        **parsed['meta'],
        'synced_at': enrichment['cve_org']['synced_at'],
        'updated_fields': updated,
        'kept_fields': kept,
    }


def vulnerabilities_cve_fill_quick_add(vulnerability: Vulnerability, user_id):
    """Best effort fill of an entry a finding just quick-added (title still
    the bare identifier, never synced). Failures are logged, never raised:
    the finding is recorded either way and the entry can be synced later."""
    if not vulnerabilities_cve_sync_enabled() or vulnerability.is_private:
        return
    if not _CVE_RE.fullmatch(vulnerability.identifier or '') or vulnerability.title != vulnerability.identifier:
        return
    if isinstance(vulnerability.enrichment, dict) and 'cve_org' in vulnerability.enrichment:
        return
    timeout = float(app.config.get('CVE_API_TIMEOUT_SECONDS', 10)) * _QUICK_ADD_TIMEOUT_RATIO
    try:
        vulnerabilities_cve_sync(vulnerability, user_id, timeout=timeout)
    except BusinessProcessingError as e:
        logger.info(f'CVE quick-add fill of {vulnerability.identifier} skipped: {e.get_message()}')
