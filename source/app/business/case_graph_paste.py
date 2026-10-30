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

"""Indicator extraction from pasted free text (§4.5), pure.

`case_graph_paste_extract` finds URLs, e-mail addresses, IPv4 / IPv6
addresses, MD5 / SHA1 / SHA256 hashes and domain names (common defanging
such as `hxxp://` and `[.]` is undone). `case_graph_paste_plan` sorts
them into the IOCs to create (type picked against the IOC type
catalogue by `ioc_type_detect_pick`), the existing IOCs of the case to
reuse and the values without a known type.
"""

import ipaddress
import re

from app.business.ioc_type_detect import ioc_type_detect_pick


CASE_GRAPH_PASTE_MAX_TEXT = 1024 * 1024

_REFANG = (
    (re.compile(r'\bhxxp(s?)://', re.IGNORECASE), r'http\1://'),
    (re.compile(r'\bfxp://', re.IGNORECASE), 'ftp://'),
    (re.compile(r'\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)', re.IGNORECASE), '.'),
    (re.compile(r'\[@\]|\[at\]|\(at\)', re.IGNORECASE), '@'),
    (re.compile(r'\[:\]'), ':'),
    (re.compile(r'\[://\]'), '://'),
)

_URL = re.compile(r'\b[a-z][a-z0-9+.-]{1,15}://[^\s<>"\'`]+', re.IGNORECASE)
_EMAIL = re.compile(r'\b[a-z0-9._%+-]+@(?:[a-z0-9-]+\.)+[a-z]{2,63}\b', re.IGNORECASE)
_IPV4 = re.compile(r'(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])')
_IPV6_CANDIDATE = re.compile(r'(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![0-9a-f:])', re.IGNORECASE)
_HASH = re.compile(r'(?<![0-9a-f])(?:[0-9a-f]{64}|[0-9a-f]{40}|[0-9a-f]{32})(?![0-9a-f])', re.IGNORECASE)
_DOMAIN = re.compile(r'(?<![\w.@-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}(?![\w-])',
                     re.IGNORECASE)
_URL_TRAILING = '.,;:!?)]}\'"'

# Extensions that make `name.ext` a file name rather than a domain.
_FILE_EXTENSIONS = frozenset({
    'exe', 'dll', 'sys', 'bat', 'cmd', 'ps1', 'psm1', 'vbs', 'vbe', 'js', 'jse', 'wsf', 'hta', 'scr', 'msi',
    'lnk', 'txt', 'log', 'pdf', 'doc', 'docx', 'docm', 'xls', 'xlsx', 'xlsm', 'ppt', 'pptx', 'rtf', 'csv',
    'json', 'xml', 'yml', 'yaml', 'ini', 'cfg', 'conf', 'tmp', 'dat', 'bin', 'iso', 'img', 'rar', '7z', 'gz',
    'tar', 'tgz', 'bz2', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'html', 'htm', 'php', 'asp', 'aspx', 'jsp',
    'py', 'sh', 'pl', 'rb', 'jar', 'class', 'evtx', 'etl', 'pf', 'db', 'sqlite', 'reg', 'cab', 'eml', 'msg',
})


def _refang(text):
    for pattern, replacement in _REFANG:
        text = pattern.sub(replacement, text)
    return text


def _mask(text, start, end):
    return text[:start] + ' ' * (end - start) + text[end:]


def _is_ipv6(value):
    if value.count(':') < 2:
        return False
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return True


def case_graph_paste_extract(text):
    """Indicator values found in `text`, deduplicated (case-insensitive),
    in order of appearance."""
    if not isinstance(text, str) or not text.strip():
        return []
    text = _refang(text)
    found = []

    def _collect(pattern, validate=None, clean=None):
        nonlocal text
        for match in list(pattern.finditer(text)):
            value = match.group(0)
            if clean is not None:
                value = clean(value)
            if validate is not None and not validate(value):
                continue
            found.append((match.start(), value))
            text = _mask(text, match.start(), match.end())

    _collect(_URL, clean=lambda value: value.rstrip(_URL_TRAILING))
    _collect(_EMAIL)
    _collect(_HASH)
    _collect(_IPV4)
    _collect(_IPV6_CANDIDATE, validate=_is_ipv6)
    _collect(_DOMAIN, validate=lambda value: value.rsplit('.', 1)[-1].lower() not in _FILE_EXTENSIONS)

    values = []
    seen = set()
    for _, value in sorted(found, key=lambda item: item[0]):
        lowered = value.lower()
        if value and lowered not in seen:
            seen.add(lowered)
            values.append(value)
    return values


def case_graph_paste_plan(values, ioc_types, existing_iocs):
    """Sort extracted values into `{create: [(value, ioc_type)],
    existing: [ioc_id], ignored: [value]}`.

    `ioc_types`: dicts with `type_id`, `type_name`. `existing_iocs`: dicts
    with `ioc_id`, `ioc_value` (the IOCs of the case; a value matches
    case-insensitively, whatever its type)."""
    existing_by_value = {}
    for ioc in existing_iocs or []:
        value = (ioc.get('ioc_value') or '').strip().lower()
        if value:
            existing_by_value.setdefault(value, ioc['ioc_id'])
    plan = {'create': [], 'existing': [], 'ignored': []}
    for value in values:
        existing = existing_by_value.get(value.lower())
        if existing is not None:
            if existing not in plan['existing']:
                plan['existing'].append(existing)
            continue
        ioc_type = ioc_type_detect_pick(value, ioc_types)
        if ioc_type is None:
            plan['ignored'].append(value)
            continue
        plan['create'].append((value, ioc_type))
    return plan
