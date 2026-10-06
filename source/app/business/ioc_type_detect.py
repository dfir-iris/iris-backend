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

"""Best-effort IOC type detection from a raw value.

Server-side port of the SPA helper `src/lib/utils/ioc-type-detect.ts`
(same rules, same order) so chat commands such as `/ioc <value>` can
pick a type without a round-trip. Candidates are MISP type names, most
specific first; the caller keeps the first one the instance actually
has. The scope business layer still validates the value against the
chosen type's regex.
"""

import ipaddress
import re


_IPV4 = re.compile(r'^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$')
_PORT = re.compile(r'^\d{1,5}$')
_HEX = re.compile(r'^[0-9a-f]+$', re.IGNORECASE)
_URL_SCHEME = re.compile(r'^[a-z][a-z0-9+.-]*://\S+$', re.IGNORECASE)
_EMAIL = re.compile(r'^[^\s@]+@[^\s@]+\.[a-z]{2,}$', re.IGNORECASE)
_MAC = re.compile(r'^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$', re.IGNORECASE)
_CVE = re.compile(r'^CVE-\d{4}-\d{4,}$', re.IGNORECASE)
_REGKEY = re.compile(r'^(HKLM|HKCU|HKCR|HKU|HKCC|HKEY_[A-Z_]+)\\', re.IGNORECASE)
_BTC = re.compile(r'^(bc1[a-z0-9]{25,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$')
_ASN = re.compile(r'^AS\d{1,10}$', re.IGNORECASE)
_DOMAIN = re.compile(r'^(?=.{1,253}$)([a-z0-9_]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$', re.IGNORECASE)
_WHITESPACE = re.compile(r'\s')

_HASH_BY_LENGTH = {
    32: 'md5',
    40: 'sha1',
    56: 'sha224',
    64: 'sha256',
    96: 'sha384',
    128: 'sha512',
}


def _is_ipv6(value):
    if ':' not in value or not re.fullmatch(r'[0-9a-fA-F:.]+', value):
        return False
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return True


def ioc_type_detect_is_ip(value):
    """True for a bare IPv4 or IPv6 address."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    return bool(_IPV4.match(value)) or _is_ipv6(value)


def ioc_type_detect_candidates(raw):
    """Candidate type names for `raw`, most specific first ([] if unknown)."""
    if not isinstance(raw, str):
        return []
    value = raw.strip()
    if not value or _WHITESPACE.search(value):
        return []

    if ioc_type_detect_is_ip(value):
        return ['ip-any', 'ip-dst', 'ip-src']

    port_sep = value.rfind(':')
    if port_sep > 0 and _IPV4.match(value[:port_sep]) and _PORT.match(value[port_sep + 1:]):
        return ['ip-dst|port', 'ip-src|port']

    if _URL_SCHEME.match(value):
        return ['url', 'uri', 'link']
    if _EMAIL.match(value):
        return ['email', 'email-src', 'email-dst']
    if _MAC.match(value):
        return ['mac-address']
    if _CVE.match(value):
        return ['vulnerability']
    if _REGKEY.match(value):
        return ['regkey']
    if _HEX.match(value) and len(value) in _HASH_BY_LENGTH:
        return [_HASH_BY_LENGTH[len(value)]]
    if _BTC.match(value):
        return ['btc']
    if _ASN.match(value):
        return ['AS']
    if _DOMAIN.match(value):
        return ['domain', 'hostname']
    return []


def ioc_type_detect_pick(raw, ioc_types):
    """First candidate the instance knows, as the matching `ioc_types` item.

    `ioc_types` is an iterable of dicts carrying `type_id` and
    `type_name`; names are compared case-insensitively. None when no
    candidate exists on this instance.
    """
    by_name = {}
    for ioc_type in ioc_types or []:
        name = ioc_type.get('type_name')
        if isinstance(name, str):
            by_name.setdefault(name.lower(), ioc_type)
    for name in ioc_type_detect_candidates(raw):
        match = by_name.get(name.lower())
        if match is not None:
            return match
    return None
